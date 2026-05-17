#!/usr/local/lib/hermes-agent/venv/bin/python3
"""
YouTube Pipeline Unificado — 3 estrategias en cascada.

Uso:  python3 pipeline.py <youtube_url>
Salida: JSON a stdout con title, video_id, slug, duration, lang,
        raw_segments, blocks, clean_transcript, method.

E1 → YouTube Transcript API (rápido, gold standard)
E3 → yt-dlp + faster-whisper chunked (10 min por chunk, modelo cargado una vez)
E2 → yt-dlp + faster-whisper whole file (último recurso)

El audio descargado se elimina tras la transcripción.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

# Unbuffer stdout so background-run output is visible
sys.stdout.reconfigure(line_buffering=True)

# Ensure venv/bin is on PATH for subprocess calls (yt-dlp, ffmpeg)
_known_venv_bin = "/usr/local/lib/hermes-agent/venv/bin"
if os.path.isdir(_known_venv_bin):
    os.environ['PATH'] = f"{_known_venv_bin}:{os.environ.get('PATH', '')}"


# ─── Helpers ────────────────────────────────────────────────────────────────

VIDEO_ID_RE = re.compile(
    r'(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([\w-]{11})'
)

def extract_video_id(url: str) -> str | None:
    m = VIDEO_ID_RE.search(url)
    return m.group(1) if m else None

def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r'[^a-z0-9áéíóúñü ]+', ' ', s)
    s = re.sub(r'\s+', '-', s).strip('-')
    return s[:60].rstrip('-')

def fmt_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def get_video_title(video_id: str) -> str:
    """Obtener título vía yt-dlp (fallback a scraping HTML)."""
    try:
        result = subprocess.run(
            ['yt-dlp', '--print', 'title',
             '--extractor-args', 'youtube:js_es=deno',
             f'https://youtube.com/watch?v={video_id}'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Fallback: scrape HTML title
    try:
        import urllib.request
        with urllib.request.urlopen(f'https://youtube.com/watch?v={video_id}', timeout=10) as r:
            html = r.read().decode('utf-8', errors='replace')
        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            return m.group(1).replace(' - YouTube', '').strip()
    except Exception:
        pass
    return f"Video {video_id}"


# ─── E1: YouTube Transcript API ─────────────────────────────────────────────

def strategy_api(video_id: str) -> dict | None:
    """Fetch via youtube-transcript-api. Returns raw segments or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        available = list(api.list(video_id))
        langs = sorted(set(
            getattr(t, 'language_code', 'en') for t in available
        ))
        # Prefer: manual > auto-generated, user language > English
        transcript = api.fetch(video_id, languages=langs)
        segments = []
        for entry in transcript:
            segments.append({
                "start": entry.start,
                "duration": entry.duration,
                "text": entry.text.strip()
            })
        lang = getattr(transcript, 'language_code', langs[0] if langs else 'unknown')
        return {"segments": segments, "lang": lang, "method": "youtube-api"}
    except Exception as e:
        return None


# ─── E3 + E2: faster-whisper ────────────────────────────────────────────────

WHISPER_MODEL = "tiny"  # rápido para español/VO, 5× más rápido que base

def _run_whisper(audio_path: str, model_name: str = WHISPER_MODEL) -> tuple[list[dict], str]:
    """Ejecuta faster-whisper y devuelve (segmentos [{start, duration, text}, ...], lang)."""
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio_path, beam_size=5)
    segments = [
        {"start": s.start, "duration": s.end - s.start, "text": s.text.strip()}
        for s in segs
    ]
    return segments, info.language


def strategy_chunked(video_id: str) -> dict | None:
    """
    E3: Descarga audio, divide en chunks de 10 min, transcribe cada uno,
    reconstruye segmentos con timestamps reales. Elimina audio al final.
    """
    audio_path = None
    try:
        # Descargar audio
        audio_path = _download_audio(video_id)
        if not audio_path:
            return None

        # Obtener duración total con ffprobe
        dur_result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', audio_path],
            capture_output=True, text=True, timeout=30
        )
        total_dur = float(dur_result.stdout.strip() or 0)
        chunk_sec = 600  # 10 min

        if total_dur <= chunk_sec:
            # Video corto, transcribir completo
            segments, lang = _run_whisper(audio_path)
            return {"segments": segments, "lang": lang, "method": "whisper-chunked"}
        
        # Dividir audio con ffmpeg segment
        tmpdir = tempfile.mkdtemp(prefix='whisper_chunks_')
        pattern = os.path.join(tmpdir, 'chunk_%03d.mp3')
        subprocess.run(
            ['ffmpeg', '-i', audio_path, '-f', 'segment', '-segment_time', '600',
             '-c', 'copy', pattern],
            capture_output=True, text=True, timeout=total_dur + 60
        )

        # Cargar modelo UNA SOLA VEZ y transcribir cada chunk
        from faster_whisper import WhisperModel
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        all_segments = []
        chunk_base = 0
        detected_lang = "unknown"
        chunk_files = sorted(
            f for f in os.listdir(tmpdir) if f.startswith('chunk_')
        )
        for cf in chunk_files:
            cpath = os.path.join(tmpdir, cf)
            segs, info = model.transcribe(cpath, beam_size=5)
            if detected_lang == "unknown":
                detected_lang = info.language
            for s in segs:
                all_segments.append({
                    "start": s.start + chunk_base,
                    "duration": s.end - s.start,
                    "text": s.text.strip()
                })
            chunk_base += chunk_sec

        lang = detected_lang

        # Limpiar chunks
        for cf in chunk_files:
            try:
                os.remove(os.path.join(tmpdir, cf))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

        return {"segments": all_segments, "lang": lang, "method": "whisper-chunked"}

    except Exception as e:
        return None
    finally:
        import shutil
        if audio_path:
            shutil.rmtree(os.path.dirname(audio_path), ignore_errors=True)


def strategy_whole(video_id: str) -> dict | None:
    """
    E2: Último recurso — descarga audio completo, transcribe en una pasada.
    """
    audio_path = None
    try:
        audio_path = _download_audio(video_id)
        if not audio_path:
            return None
        segments, lang = _run_whisper(audio_path)
        return {"segments": segments, "lang": lang, "method": "whisper-whole"}
    except Exception as e:
        return None
    finally:
        import shutil
        if audio_path:
            shutil.rmtree(os.path.dirname(audio_path), ignore_errors=True)


def _download_audio(video_id: str) -> str | None:
    """Descarga audio a un directorio temporal. Devuelve path o None."""
    outdir = tempfile.mkdtemp(prefix='yt_audio_')
    outtmpl = os.path.join(outdir, '%(id)s.%(ext)s')
    result = subprocess.run(
        ['yt-dlp', '-x', '--audio-format', 'mp3', '-o', outtmpl,
         '--extractor-args', 'youtube:js_es=deno',
         f'https://youtube.com/watch?v={video_id}'],
        capture_output=True, text=True, timeout=1800
    )
    if result.returncode != 0:
        # Cleanup on failure
        import shutil
        shutil.rmtree(outdir, ignore_errors=True)
        return None
    expected = os.path.join(outdir, f"{video_id}.mp3")
    if os.path.exists(expected):
        return expected
    # Fallback: find any audio file in the temp dir
    for f in os.listdir(outdir):
        fp = os.path.join(outdir, f)
        if os.path.isfile(fp) and not f.endswith('.part'):
            return fp
    return None


def _detect_lang(segments: list[dict]) -> str:
    """Detecta idioma de los segmentos. Placeholder — no usado actualmente."""
    return 'unknown'


# ─── QC ─────────────────────────────────────────────────────────────────────

def qc_check(segments: list[dict]) -> dict:
    """Quality control: cobertura, orden cronológico, integridad."""
    if not segments:
        return {"coverage": 0.0, "chronological": False, "integrity": False,
                "seg_count": 0, "total_sec": 0}

    total_sec = segments[-1]["start"] + segments[-1]["duration"]
    chronological = all(
        segments[i]["start"] <= segments[i+1]["start"]
        for i in range(len(segments)-1)
    )

    expected = max(1, int(total_sec / 3))  # ~1 seg cada 3s
    actual = len(segments)
    integrity = actual >= expected * 0.5  # al menos 50% de lo esperado

    # Coverage: qué % del tiempo total está cubierto por segmentos
    covered = sum(s["duration"] for s in segments)
    coverage = min(100.0, round(covered / total_sec * 100, 1)) if total_sec > 0 else 0

    return {
        "coverage": coverage,
        "chronological": chronological,
        "integrity": integrity,
        "seg_count": actual,
        "total_sec": round(total_sec, 1)
    }


# ─── Cleanup ────────────────────────────────────────────────────────────────

def cleanup(segments: list[dict]) -> dict:
    """
    Agrupa en bloques de ~30s con marca ### [MM:SS].
    Devuelve: {blocks: [{ts, text}], clean_transcript: str, raw_segments: [...]}
    """
    if not segments:
        return {"blocks": [], "clean_transcript": "", "raw_segments": []}

    blocks = []
    current_block = {"ts": segments[0]["start"], "texts": []}

    for s in segments:
        # Nuevo bloque si el gap es > 25s
        if current_block["texts"] and s["start"] - current_block["ts"] > 25:
            full_text = " ".join(current_block["texts"])
            blocks.append({
                "ts": fmt_ts(current_block["ts"]),
                "text": full_text
            })
            current_block = {"ts": s["start"], "texts": [s["text"]]}
        else:
            current_block["texts"].append(s["text"])

    # Último bloque
    if current_block["texts"]:
        full_text = " ".join(current_block["texts"])
        blocks.append({
            "ts": fmt_ts(current_block["ts"]),
            "text": full_text
        })

    # Construir transcript limpio
    clean_parts = []
    for b in blocks:
        wrapped = textwrap.fill(b["text"], width=80)
        clean_parts.append(f"### [{b['ts']}]\n\n{wrapped}")

    raw_segments = [
        {"start": s["start"], "text": s["text"]}
        for s in segments
    ]

    return {
        "blocks": blocks,
        "clean_transcript": "\n\n".join(clean_parts),
        "raw_segments": raw_segments
    }


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: pipeline.py <youtube_url>"}))
        sys.exit(1)

    url = sys.argv[1]
    video_id = extract_video_id(url)
    if not video_id:
        print(json.dumps({"error": f"No se pudo extraer video_id de: {url}"}))
        sys.exit(1)

    # Obtener metadata
    title = get_video_title(video_id)
    slug = slugify(title)

    # ── E1: YouTube API ──
    result = strategy_api(video_id)
    method = "youtube-api"

    # ── E3: Whisper chunked (si E1 falla) ──
    if result is None:
        result = strategy_chunked(video_id)
        method = "whisper-chunked"

    # ── E2: Whisper whole file (si E3 falla) ──
    if result is None:
        result = strategy_whole(video_id)
        method = "whisper-whole"

    if result is None:
        print(json.dumps({"error": "Todas las estrategias fallaron", "video_id": video_id}))
        sys.exit(1)

    segments = result["segments"]
    lang = result["lang"]

    # QC
    qc = qc_check(segments)

    # Cleanup
    cleaned = cleanup(segments)

    # Duración
    duration_sec = qc["total_sec"]

    output = {
        "title": title,
        "video_id": video_id,
        "slug": slug,
        "url": f"https://youtube.com/watch?v={video_id}",
        "duration_sec": duration_sec,
        "duration": fmt_duration(int(duration_sec)),
        "lang": lang,
        "method": method,
        "qc": qc,
        "raw_segments": cleaned["raw_segments"],
        "blocks": cleaned["blocks"],
        "clean_transcript": cleaned["clean_transcript"]
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
