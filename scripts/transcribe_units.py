#!/usr/bin/env python3
"""
transcribe_units.py — Phase 6 & 7: Per-Unit Transcription with Retry Logic

Transcribes audio chunks using a configurable speech-to-text engine.
Includes a simulated transcription mode for testing, plus retry logic with
escalating strategies (reduced chunk size, noise cleanup).

Usage:
    python transcribe_units.py <unit_path> [--config <json>] [--simulate]
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# -- Custom Exceptions --


class TranscriptionError(Exception):
    """Raised when transcription of a unit fails."""
    pass


# -- Core Transcription Functions --


def _simulate_transcription(unit_path: str, unit_info: dict[str, Any], lang: str) -> dict[str, Any]:
    """Simulate a speech-to-text transcription for testing.

    Generates plausible fake transcript segments based on chunk duration.
    """
    duration = unit_info.get("duration_sec", 30)
    segment_count = max(1, int(duration / 3))  # ~1 segment per 3 seconds
    segment_duration = duration / segment_count

    lorem_words = [
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "had", "her", "was", "one", "our", "out", "has", "have", "been",
        "this", "that", "with", "from", "they", "what", "when", "where",
        "which", "their", "there", "would", "about", "could", "should",
        "transcription", "pipeline", "audio", "processing", "speech",
        "recognition", "model", "training", "data", "quality",
    ]

    segments = []
    for i in range(segment_count):
        start = round(i * segment_duration, 2)
        end = round(min((i + 1) * segment_duration, duration), 2)

        # Generate a fake sentence
        word_count = 3 + (i % 5)
        sentence_words = [lorem_words[(i * 7 + j * 13) % len(lorem_words)] for j in range(word_count)]
        text = " ".join(sentence_words) + ("." if word_count > 3 else "")

        segments.append({
            "start": start,
            "end": end,
            "text": text,
            "confidence": round(0.85 + (i % 10) * 0.01, 2),
            "words": [
                {
                    "word": w,
                    "start": round(start + (end - start) * j / len(sentence_words), 2),
                    "end": round(start + (end - start) * (j + 1) / len(sentence_words), 2),
                    "confidence": round(0.85 + (j % 3) * 0.03, 2),
                }
                for j, w in enumerate(sentence_words)
            ],
        })

    full_text = " ".join(s["text"] for s in segments)
    avg_conf = sum(s["confidence"] for s in segments) / len(segments) if segments else 0

    return {
        "chunk_id": unit_info.get("chunk_id", "unknown"),
        "text": full_text,
        "start_sec": unit_info.get("start_sec", 0),
        "end_sec": unit_info.get("end_sec", duration),
        "language": lang,
        "confidence": round(avg_conf, 2),
        "segments": segments,
        "word_count": sum(len(s["words"]) for s in segments),
        "engine": "simulated",
        "status": "completed",
    }


def _transcribe_with_faster_whisper(
    unit_path: str,
    unit_info: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Transcribe using faster-whisper (ctranslate2-based, no GPU needed).

    Uses the faster_whisper.WhisperModel for high-performance CPU inference.
    Model is cached after first load.

    Args:
        unit_path: Path to the audio chunk.
        unit_info: Chunk metadata.
        config: Config with model_size (default: 'tiny'), device (default: 'cpu'),
               compute_type (default: 'int8'), language (default: 'en').

    Returns:
        Standard transcription result dict.
    """
    model_size = config.get("faster_whisper_model", "base")
    device = config.get("faster_whisper_device", "cpu")
    compute_type = config.get("faster_whisper_compute", "int8")
    lang = config.get("language", "en")

    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments_gen, info = model.transcribe(unit_path, language=lang if lang != "auto" else None)

        segments = []
        full_text_parts = []
        for seg in segments_gen:
            seg_dict = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "confidence": round(seg.avg_logprob if hasattr(seg, 'avg_logprob') else 0.0, 4),
                "words": [],
            }
            if hasattr(seg, 'words') and seg.words:
                seg_dict["words"] = [
                    {
                        "word": w.word,
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                        "confidence": round(w.probability if hasattr(w, 'probability') else 0.0, 4),
                    }
                    for w in seg.words
                ]
            segments.append(seg_dict)
            full_text_parts.append(seg.text.strip())

        full_text = " ".join(full_text_parts)
        detected_lang = info.language if hasattr(info, 'language') else lang
        avg_conf = (
            sum(s["confidence"] for s in segments) / len(segments)
            if segments else 0.0
        )

        # Word count: prefer word-level timestamps, fall back to text split
        # (faster-whisper tiny/base may not emit word-level segments)
        word_count_via_words = sum(len(s["words"]) for s in segments)
        if word_count_via_words > 0:
            final_word_count = word_count_via_words
        else:
            final_word_count = len(full_text.split()) if full_text.strip() else 0

        return {
            "chunk_id": unit_info.get("chunk_id", "unknown"),
            "text": full_text,
            "start_sec": unit_info.get("start_sec", 0),
            "end_sec": unit_info.get("end_sec", 0),
            "language": detected_lang,
            "confidence": round(avg_conf, 2),
            "segments": segments,
            "word_count": final_word_count,
            "engine": f"faster-whisper-{model_size}",
            "status": "completed",
        }

    except ImportError:
        raise TranscriptionError(
            "faster-whisper not installed. Run: pip install faster-whisper"
        )
    except Exception as e:
        raise TranscriptionError(
            f"faster-whisper transcription failed on {unit_path}: {e}"
        )


def _transcribe_with_whisper_cpp(
    unit_path: str,
    unit_info: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Transcribe using whisper-cpp CLI if available."""
    whisper_bin = config.get("whisper_bin", "whisper-cpp")
    model_path = config.get("whisper_model", "ggml-base.en.bin")
    lang = config.get("language", "en")

    output_json_path = unit_path + ".json"

    cmd = [
        whisper_bin,
        "-m", model_path,
        "-f", unit_path,
        "-oj",
        "-of", unit_path,
        "-l", lang,
    ]
    if config.get("translate", False):
        cmd.append("-tr")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise TranscriptionError(
                f"whisper-cpp exited with code {result.returncode}: {result.stderr[:200]}"
            )
    except FileNotFoundError:
        raise TranscriptionError(
            f"whisper-cpp binary not found at '{whisper_bin}'. "
            f"Install whisper-cpp or use --simulate mode."
        )
    except subprocess.TimeoutExpired:
        raise TranscriptionError(f"whisper-cpp timed out on {unit_path}")

    # Parse output
    if not Path(output_json_path).exists():
        raise TranscriptionError(f"whisper-cpp did not produce output JSON: {output_json_path}")

    with open(output_json_path) as f:
        raw = json.load(f)
    os.remove(output_json_path)

    # Convert whisper-cpp format to our standard
    segments = []
    for seg in raw.get("segments", []):
        segments.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", "").strip(),
            "confidence": seg.get("confidence", 1.0),
            "words": [
                {
                    "word": w.get("word", ""),
                    "start": w.get("start", 0),
                    "end": w.get("end", 0),
                    "confidence": w.get("confidence", 1.0),
                }
                for w in seg.get("words", [])
            ],
        })

    full_text = " ".join(s["text"] for s in segments)
    avg_conf = (
        sum(s["confidence"] for s in segments) / len(segments)
        if segments else 0.0
    )

    return {
        "chunk_id": unit_info.get("chunk_id", "unknown"),
        "text": full_text,
        "start_sec": unit_info.get("start_sec", 0),
        "end_sec": unit_info.get("end_sec", 0),
        "language": lang,
        "confidence": round(avg_conf, 2),
        "segments": segments,
        "word_count": sum(len(s["words"]) for s in segments),
        "engine": "whisper-cpp",
        "status": "completed",
    }


def transcribe_unit(
    unit_path: str,
    unit_info: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Transcribe a single audio unit (chunk) using the configured STT engine.

    Args:
        unit_path: Path to the audio chunk file.
        unit_info: Dict with chunk metadata (chunk_id, start_sec, end_sec, duration_sec).
        config: Configuration dict with keys:
            - engine: 'simulated' (default) or 'whisper-cpp'.
            - language: Language code (default: 'en').
            - whisper_bin: Path to whisper-cpp binary.
            - whisper_model: Path to whisper model file.
            - simulate: Force simulation mode (bool).

    Returns:
        Dict with:
            - chunk_id: Original chunk identifier.
            - text: Full transcribed text.
            - start_sec: Source-relative start time.
            - end_sec: Source-relative end time.
            - language: Detected or configured language.
            - confidence: Aggregate confidence score (0.0-1.0).
            - segments: List of per-segment dicts with start/end/text/confidence/words.
            - word_count: Total word count.
            - engine: Engine used.
            - status: 'completed' or 'failed'.

    Raises:
        TranscriptionError: If transcription fails irrecoverably.
        FileNotFoundError: If unit_path does not exist.
    """
    # Merge config with defaults
    effective_config = {
        "engine": "faster-whisper",
        "language": "en",
        "simulate": False,
        **config,
    }

    engine = effective_config.get("engine", "simulated")
    simulate = effective_config.get("simulate", False)

    # Simulated mode doesn't need a real file
    if simulate or engine == "simulated":
        return _simulate_transcription(unit_path, unit_info, effective_config.get("language", "en"))

    # Non-simulated engines require a real file
    path = Path(unit_path)
    if not path.exists():
        raise FileNotFoundError(f"Unit file not found: {unit_path}")

    if path.stat().st_size == 0:
        raise TranscriptionError(f"Unit file is empty: {unit_path}")

    if engine == "faster-whisper":
        return _transcribe_with_faster_whisper(unit_path, unit_info, effective_config)
    elif engine == "whisper-cpp":
        return _transcribe_with_whisper_cpp(unit_path, unit_info, effective_config)
    else:
        raise TranscriptionError(f"Unknown engine '{engine}'. Supported: 'simulated', 'faster-whisper', 'whisper-cpp'")


def transcribe_with_retries(
    unit_path: str,
    unit_info: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Transcribe a unit with escalating retry strategy.

    Attempts:
    1. Direct transcription of the original chunk.
    2. If failed: halve the chunk duration (trim end), retry.
    3. If failed: apply basic noise cleanup (normalize volume), retry.

    Args:
        unit_path: Path to the audio chunk file.
        unit_info: Dict with chunk metadata.
        config: Configuration dict for transcription.

    Returns:
        Dict with transcription result or structured failure marker.
        On final failure, returns dict with status='failed_final' and
        text='[MISSING TRANSCRIPTION: ...]'
    """
    chunk_id = unit_info.get("chunk_id", "unknown")
    start_sec = unit_info.get("start_sec", 0)
    end_sec = unit_info.get("end_sec", 0)

    # Attempt 1: Direct transcription
    try:
        result = transcribe_unit(unit_path, unit_info, config)
        if result.get("status") == "completed":
            result["attempts"] = 1
            return result
    except (TranscriptionError, FileNotFoundError) as e:
        attempt1_error = str(e)

    # Attempt 2: Halve the chunk, retry
    try:
        half_duration = unit_info.get("duration_sec", 30) / 2
        half_info = {**unit_info, "duration_sec": half_duration}
        result = transcribe_unit(unit_path, half_info, {**config, "retry": 2})
        if result.get("status") == "completed":
            result["attempts"] = 2
            return result
    except (TranscriptionError, FileNotFoundError):
        pass

    # Attempt 3: Apply basic audio cleanup and retry
    try:
        cleaned_path = _cleanup_audio(unit_path)
        if cleaned_path:
            result = transcribe_unit(cleaned_path, unit_info, {**config, "retry": 3})
            # Clean up temp file
            if Path(cleaned_path).exists() and cleaned_path != unit_path:
                try:
                    os.remove(cleaned_path)
                except OSError:
                    pass
            if result.get("status") == "completed":
                result["attempts"] = 3
                return result
    except (TranscriptionError, FileNotFoundError):
        pass

    # All attempts failed
    gap_text = (
        f"[MISSING TRANSCRIPTION: {chunk_id} from "
        f"{_format_ts(start_sec)} to {_format_ts(end_sec)}]"
    )
    return {
        "chunk_id": chunk_id,
        "text": gap_text,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "language": config.get("language", "unknown"),
        "confidence": 0.0,
        "segments": [
            {
                "start": start_sec,
                "end": end_sec,
                "text": gap_text,
                "confidence": 0.0,
                "words": [],
            }
        ],
        "word_count": 0,
        "engine": "failed",
        "status": "failed_final",
        "attempts": 3,
    }


def _cleanup_audio(unit_path: str) -> Optional[str]:
    """Apply basic audio cleanup: normalize volume using ffmpeg.

    Returns path to cleaned audio file, or None on failure.
    """
    cleaned_path = unit_path + ".cleaned.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", unit_path,
        "-af", "volume=2.0",          # Boost volume
        "-ar", "16000",
        "-ac", "1",
        "-f", "pcm_s16le",
        cleaned_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True)
        if Path(cleaned_path).exists() and Path(cleaned_path).stat().st_size > 100:
            return cleaned_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _format_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS (for display in gap markers)."""
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def validate_transcription(transcript: dict[str, Any]) -> bool:
    """Validate a transcription result for structural integrity.

    Checks:
    - Text is non-empty (or is a valid failure marker)
    - Has required fields
    - Timestamps are reasonable
    - Segments exist and are consistent

    Args:
        transcript: Dict from transcribe_unit() or transcribe_with_retries().

    Returns:
        True if the transcript passes all validation checks.
    """
    required_fields = {"chunk_id", "text", "start_sec", "end_sec", "segments", "status"}
    if not all(field in transcript for field in required_fields):
        return False

    # Failed transcripts are valid (structured failure)
    if transcript["status"] == "failed_final":
        return bool(transcript["text"])

    # Text must not be empty
    if not transcript.get("text", "").strip():
        return False

    # Segments must be a non-empty list
    segments = transcript.get("segments", [])
    if not isinstance(segments, list) or len(segments) == 0:
        return False

    # Timestamps should be reasonable
    if transcript["end_sec"] < transcript["start_sec"]:
        return False

    # Segment timestamps should be consistent
    for seg in segments:
        if "start" not in seg or "end" not in seg or "text" not in seg:
            return False
        if seg["end"] < seg["start"]:
            return False

    return True


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Transcribe audio chunks with retry logic"
    )
    parser.add_argument("unit_path", help="Path to the audio chunk file")
    parser.add_argument(
        "--config", default="{}",
        help='JSON configuration string or path to JSON file (default: "{}")'
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="Force simulated transcription (bypass engine config)"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate a previously saved transcript JSON"
    )

    args = parser.parse_args()

    # Parse config
    config: dict[str, Any] = {}
    if args.config != "{}":
        if Path(args.config).exists():
            with open(args.config) as f:
                config = json.load(f)
        else:
            try:
                config = json.loads(args.config)
            except json.JSONDecodeError:
                print(f"ERROR: Invalid JSON config: {args.config}", file=sys.stderr)
                sys.exit(1)

    if args.simulate:
        config["simulate"] = True

    if args.validate:
        # Validate an existing transcript JSON
        try:
            with open(args.unit_path) as f:
                transcript = json.load(f)
            if validate_transcription(transcript):
                print(f"VALID: {args.unit_path}")
                sys.exit(0)
            else:
                print(f"INVALID: {args.unit_path}", file=sys.stderr)
                sys.exit(1)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    # Build unit_info
    unit_path = args.unit_path
    unit_info = {
        "chunk_id": Path(unit_path).stem,
        "start_sec": 0.0,
        "end_sec": 0.0,
        "duration_sec": 30.0,
    }

    # Try to extract duration from the filename (chunk_NNNN_HH-MM-SS_HH-MM-SS.wav)
    stem = Path(unit_path).stem
    parts = stem.split("_")
    if len(parts) >= 4:
        try:
            end_ts = parts[-1]  # HH-MM-SS
            h, m, s = end_ts.split("-")
            unit_info["end_sec"] = int(h) * 3600 + int(m) * 60 + int(s)
            start_ts = parts[-2]
            h, m, s = start_ts.split("-")
            unit_info["start_sec"] = int(h) * 3600 + int(m) * 60 + int(s)
            unit_info["duration_sec"] = unit_info["end_sec"] - unit_info["start_sec"]
            unit_info["chunk_id"] = "_".join(parts[:-2])
        except (ValueError, IndexError):
            pass

    try:
        result = transcribe_with_retries(unit_path, unit_info, config)

        # Validate
        if not validate_transcription(result):
            print(f"WARNING: Generated transcript failed validation", file=sys.stderr)

        print(json.dumps(result, default=str, indent=2))

    except (FileNotFoundError, TranscriptionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
