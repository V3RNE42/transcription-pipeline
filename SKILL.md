---
name: youtube-content
description: "YouTube transcripts to summaries, threads, blogs."
platforms: [linux, macos, windows]
---

# YouTube Content Tool

## When to use

Use when the user shares a YouTube URL or video link, asks to summarize a video, requests a transcript, or wants to extract and reformat content from any YouTube video. Transforms transcripts into structured content (chapters, summaries, threads, blog posts).

Extract transcripts from YouTube videos and convert them into useful formats.

## Setup

```bash
pip install youtube-transcript-api
```

> **Note:** `youtube-transcript-api` v2.x changed its API. The script `scripts/fetch_transcript.py` has been updated to use the new instantiation-based API. If you get errors calling `YouTubeTranscriptApi.get_transcript()` directly, use the script instead — it handles both URL parsing and the new API correctly.

## Canonical Script (unified pipeline)

El script unificado es `scripts/pipeline.py`. Maneja las 3 estrategias en cascada, produce JSON completo con cleanup y QC. Usar para el pipeline completo.

```bash
python3 SKILL_DIR/scripts/pipeline.py "https://youtube.com/watch?v=VIDEO_ID"
```

Salida: JSON con title, video_id, slug, duration, lang, method, qc, blocks[], raw_segments[], clean_transcript.

## Legacy: fetch_transcript.py (solo obtención vía API)

Para tareas que solo necesiten el transcript crudo sin fallback ni cleanup:

```bash
# JSON output with metadata
python3 SKILL_DIR/scripts/fetch_transcript.py "https://youtube.com/watch?v=VIDEO_ID"

# Plain text (good for piping into further processing)
python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --text-only

# With timestamps
python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --timestamps

# Specific language with fallback chain
python3 SKILL_DIR/scripts/fetch_transcript.py "URL" --language tr,en
```

## Language Discovery

When the requested language is not available, use the `list()` API to discover what IS available and whether translation is possible.

```python
from youtube_transcript_api import YouTubeTranscriptApi

api = YouTubeTranscriptApi()
available = api.list("VIDEO_ID")
for t in available:
    print(t.language, t.language_code, 
          "(generated)" if t.is_generated else "(manual)",
          "(translatable)" if t.is_translatable else "")

# If the found transcript is translatable to the desired language,
# pass the source language in fetch() — the API translates automatically:
transcript = api.fetch("VIDEO_ID", languages=('es',))
# This fetches Spanish. To get English translation, if available:
# transcript = api.fetch("VIDEO_ID", languages=('en',))
# Falls back to translating from the best available source if en not native.
```

## Output Formats

After fetching the transcript, format it based on what the user asks for:

- **Chapters**: Group by topic shifts, output timestamped chapter list
- **Summary**: Concise 5-10 sentence overview of the entire video
- **Chapter summaries**: Chapters with a short paragraph summary for each
- **Thread**: Twitter/X thread format — numbered posts, each under 280 chars
- **Blog post**: Full article with title, sections, and key takeaways
- **Quotes**: Notable quotes with timestamps

### Example — Chapters Output

```
00:00 Introduction — host opens with the problem statement
03:45 Background — prior work and why existing solutions fall short
12:20 Core method — walkthrough of the proposed approach
24:10 Results — benchmark comparisons and key takeaways
31:55 Q&A — audience questions on scalability and next steps
```

## Pipeline Script — 3 Estrategias en Cascada

`scripts/pipeline.py` es el script unificado que implementa las 3 estrategias:

```
URL → E1: YouTube Transcript API (rápido, gold standard)
    → E3: yt-dlp + faster-whisper CHUNKED (10 min por chunk, modelo cargado UNA VEZ)
    → E2: yt-dlp + faster-whisper whole file (último recurso)
    → QC + cleanup (~30s blocks)
    → Eliminar audio descargado
    → JSON a stdout: title, video_id, slug, duration, lang, segments, blocks, clean_transcript, method
```

**E3 preferido sobre E2** porque:
- Chunked carga el modelo de whisper una sola vez para todos los chunks (más eficiente)
- Mejor manejo de memoria en videos largos
- Si falla un chunk, se retoma desde el siguiente (no todo perdido)

**Audio siempre eliminado** tras la transcripción. No deja residuos.

```bash
python3 SKILL_DIR/scripts/pipeline.py "https://youtube.com/watch?v=VIDEO_ID"
```

El script produce JSON. Luego el agente lee ese JSON, genera un resumen abstractivo vía LLM, escribe la nota en el vault, y entrega SOLO el resumen.

## Full Pipeline (Default — Code-First)

When the user provides a YouTube URL without specifying a format, run the **full pipeline**:

### Phase 1: Fetch

**ALWAYS discover available languages FIRST** before attempting fetch. Blindly trying `('en',)` wastes a round-trip on non-English videos.

```python
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()

# Step 1: Discover what's available
available = list(api.list("VIDEO_ID"))
langs = []
for t in available:
    lc = getattr(t, 'language_code', None)
    if lc:
        langs.append(lc)

# Step 2: Fetch with the discovered languages
# Priority: manual captions first, auto-generated second
transcript = api.fetch("VIDEO_ID", languages=langs)
```

If the primary language is translatable to a language the user wants, pass that target code — the API translates automatically:
```python
# Video has Spanish auto-generated, translatable to English
transcript = api.fetch("VIDEO_ID", languages=('en',))
# → auto-translates from Spanish to English
```

**Video title:** `scripts/pipeline.py` obtiene el título automáticamente vía `yt-dlp --print title`. `fetch_transcript.py` (legacy) NO devuelve el título. Para obtener el título sin el pipeline completo, usar `yt-dlp --print title URL` o scrapear el `<title>` de YouTube (ver `references/youtube-title-retrieval.md`). El título es necesario para el slug del vault y el encabezado de la nota.

### Phase 2: Quality Control (QC)
Run these checks before processing further:

- **Coverage**: Verify segments cover from `t[0].start` to `t[-1].start + t[-1].duration`. Full coverage = 100%.
- **Chronological order**: Verify `t[i].start <= t[i+1].start` for all i.
- **Integrity**: Verify no segments lost (count fetched vs expected).
- **Source confidence**: YouTube captions = 1.0 (gold standard).

Report results with PASS/FAIL.
### Phase 3: Cleanup (group into ~30s blocks)

Raw API output is one segment per subtitle line (~2-5s each). Group into readable blocks.

**Approach A (preferred):** `scripts/pipeline.py` hace cleanup inline automáticamente — agrupa bloques cada ~25s de gap, añade marcadores `### [MM:SS]`, aplica textwrap. No necesita scripts externos.

**Approach B (legacy):** Usar `scripts/cleanup_transcript.py` manualmente para datos ya obtenidos:
```bash
python3 SKILL_DIR/scripts/cleanup_transcript.py --input /tmp/fetch.json --output /tmp/cleanup.json
```

The script handles grouping, normalization, text wrapping, and markdown rendering. See `references/youtube_qc_guide.md` for expected YouTube caption characteristics.

Each block gets a `### [MM:SS]` marker. The full text is also kept raw for vault storage.

### Phase 4: Resumen Unificado

Generate a summary of the video content with these constraints:

| Constraint | Value |
|-----------|-------|
| Max length | **Tiered by duration**: ≤20 min → 300 chars, 20-40 min → 450 chars, 40-60 min → 800 chars, >60 min → 1000 chars |
| Content | Cover core thesis, key facts, numbers, practical takeaways |
| Format | Plain text — no bold, no headers, no emoji, no bullets |
| Self-contained | Readable without watching the video |

### Phase 5: Vault Save

If the Obsidian vault is configured (`/root/vault`), save a structured note:

```markdown
---
fecha: <date>
fuente: YouTube
url: <video_url>
video_id: <yt_id>
slug: <slug>
duracion: <MM:SS>
idioma: <language>
metodo: YouTube API + cleanup pipeline (sequential, code-first)
tags: [transcripcion, youtube, <relevant-tags>]
---

# <Title>

**Fuente:** <url>
**Duración:** <duration>
**Idioma:** <language>

## Resumen Unificado

<summary>

## Transcript Limpio

<cleaned transcript with ### [MM:SS] blocks, generated by cleanup phase>

## Metadata Técnica

- Segmentos crudos: <N>
- Bloques limpios: <N>
- Duración total: <duration>
- Cobertura: 100%
- Cleanup: bloques de ~30s, puntuación normalizada
- Control de calidad: PASS
```

Path: `$VAULT/Transcripciones/YouTube/<video_id>_<slug>.md`

**Slug generation:** Generate from the video title. Lowercase, replace spaces with hyphens, strip special characters
(keep only a-z, 0-9, hyphens). Max 60 chars. If a filename collision occurs (rare, same video_id with different slug),
append `-2`, `-3`, etc.

Example: "Using Claude Worktrees for INFINITE parallelization" → `dQw4w9WgXcQ_using-claude-worktrees-for-infinite-parallelization.md`

The vault note MUST include the full cleaned transcript (from `/tmp/pipeline_sequential/cleaned_transcript.md`), not just the summary. This is the permanent home for the transcript — no data left in /tmp/.

### Phase 6: Delivery

Deliver **exactly one message** containing ONLY the summary text. No QC report, no timing, no vault path, no pipeline metadata, no "offer to refine". The user sees only the resumen.

## Ejecution Mode: CODE-FIRST (Default)

**Rule:** Everything that can be done via code, MUST be done via code. Only use agent reasoning (LLM) for tasks that genuinely need it (summary generation, semantic decisions).

**Zero-verbosity rule:** The user sees ONLY the summary. No tool call output, no progress messages, no QC reports, no vault paths, no "offer to refine". The execute_code block and terminal calls are invisible. The only output to chat is the final summary text.

**Two-phase execution:**
1. Script does all mechanical work (fetch, fallback, cleanup, vault save, interlink) and outputs structured data to stdout.
2. Agent reads the structured data, generates a proper LLM summary (not extractive), patches the vault note with the real summary, and delivers ONLY the summary text.

**Never deliver an extractive summary** (sampled blocks from middle of transcript). The user expects a coherent, abstractive summary. If the script fails to deliver a proper summary, the agent must read the transcript and generate one.

**Deliverable:** One single message — ONLY the summary (resumen). Nothing else. No QC report, no timing, no vault path, no pipeline metadata. Just the plain text of the summary.

### Constraints

| Constraint | Value |
|-----------|-------|
| Message content | ONLY the summary text |
| Max length | **Tiered by duration**: ≤20 min → 300 chars, 20-40 min → 450 chars, 40-60 min → 800 chars, >60 min → 1000 chars |
| Visible tool calls | **Zero** — never show terminal/execute_code output in chat |
| Subagents | None — code-first for mechanical work |

### Sequential Pipeline (Default)

Write a single Python script that does ALL mechanical phases in one process in a single `execute_code` call. The agent generates the summary inline (LLM step), writes vault, and delivers ONLY the summary text as the final message. The `execute_code` call itself is invisible to the user — only the final summary text appears.

**One-call rule:** No fragmentation across tools (skill_view + terminal + execute_code). One Python script, one commit to the conversation. If you need to install a dep, do it as a silent terminal call before the script, not interleaved in the pipeline.

```python
execute_code block (invisible to user):
  ├── Fetch transcript via youtube-transcript-api
  ├── On TranscriptsDisabled → fallback to yt-dlp + faster-whisper
  ├── Run QC checks (inline)
  ├── Cleanup into ~30s blocks with ### [MM:SS] markers (inline, no external script)
  ├── Generate extractive summary (sample beginning + middle + end)
  ├── Write vault note with full transcript
  ├── Interlink with existing vault entries
  └── Print summary to stdout (becomes the deliverable)

Final message → ONLY the summary text (the script's stdout)
```

**Summary generation:**
- Extractive mode (single script): sample 3 blocks from beginning, middle, end. Join with transitions. Trim to tier limit.
- LLM mode (agent inline): script outputs structured data (blocks, title, duration), agent generates proper abstractive summary, patches vault, delivers.

**Fallback notes (whisper) — E3 (chunked) con prelación sobre E2 (whole):**
- `youtube-transcript-api` → fast, preferred. On `TranscriptsDisabled`, catch the exception and switch to whisper.
- E3 primero: divide el audio en chunks de 10 min y transcribe cada uno con el mismo modelo (cargado una sola vez). Preferido sobre E2 porque maneja mejor memoria, es más resiliente, y el modelo se carga una vez para todos los chunks.
- E2 último recurso: transcripción completa en una pasada si el chunking falla.
- El script `scripts/pipeline.py` implementa toda la lógica de cascada, QC, cleanup y JSON output automáticamente.
- Ver `references/whisper-fallback.md` para documentación detallada de: Segment API (`.end - .start`), output template, PATH, JS runtime flag, VAD filter, language detection, stdout buffering, chunked reconstruction, y audio cleanup.
- **Audio siempre eliminado** tras la transcripción exitosa. El pipeline no deja archivos temporales.

**No delegate_task.** No subagents. No visible tool output. No metadata. Only the summary.

### Post-Delivery: Vault Interlinking

After delivering the summary, scan ALL existing vault transcripts and add `[[wikilinks]]` between related ones. Invisible code step — no output shown.

**Link rules:**
- Shared agents/agents → link
- Shared models/tools → link  
- Shared themes → link
- Add links in both directions where relevant
- Wikilinks go at the end of the file under a `## Conexiones` section

**CRITICAL: APPEND-ONLY pattern.** Never read the whole file and rewrite it. Only append.

**Pitfalls (YouTube pipeline):**
- **E3 antes que E2:** Siempre intentar whispering chunked (10 min) antes que whole-file. El chunking carga el modelo una vez, es más resiliente, maneja mejor memoria. Whole-file solo como último recurso.
- **Audio cleanup obligatorio:** Eliminar el audio descargado en un bloque `finally` — usar `shutil.rmtree(os.path.dirname(audio_path), ignore_errors=True)` para limpiar el directorio temporal completo. Nunca dejar residuos.
- **yt-dlp output template (CRITICAL):** Usar un directorio temporal con el patrón `%(id)s.%(ext)s`. NO usar `NamedTemporaryFile` como output — yt-dlp no escribe en el archivo pre-creado, produce archivos de 0 bytes y falla con `WARNING: unable to obtain file audio codec with ffprobe`.
- **PATH para yt-dlp:** yt-dlp vive en `venv/bin/`. Las llamadas `subprocess.run(['yt-dlp', ...])` fallan con `FileNotFoundError` si el venv/bin no está en PATH. Añadir `os.environ['PATH'] = '/usr/local/lib/hermes-agent/venv/bin:' + os.environ.get('PATH', '')` al inicio del script.
- **yt-dlp JS runtime:** Sin `--extractor-args youtube:js_es=deno`, yt-dlp puede fallar silenciosamente en la extracción de YouTube. Especialmente crítico en entornos sin deno/node.
- **Whisper Segment API:** Los segmentos de `faster_whisper` NO tienen `.duration`. Usar `segment.end - segment.start` para calcular duración.
- **VAD filter:** NO usar `vad_filter=True` en `model.transcribe()` — salta porciones significativas del audio (reduce cobertura de ~99% a ~87%).
- **Language detection:** Capturar `info.language` del tuple `segs, info = model.transcribe(...)`. El idioma NO está disponible desde los objetos segment individuales.
- **stdout buffering:** En scripts ejecutados como background process, stdout necesita `sys.stdout.reconfigure(line_buffering=True)` o no se captura output hasta que el proceso termina.
- **Modelo cargado una vez en chunked:** Para videos > 10 min, cargar el modelo de whisper UNA SOLA VEZ y transcribir cada chunk secuencialmente. NO recargar el modelo por chunk (tarda ~2s cada carga).
- **Vault slug fallback:** If title extraction fails (HTML scrape returns nothing, yt-dlp returns empty), the slug defaults to 'video' — which is useless. Always verify slug length >= 4 chars before vault save. If short, use video_id as slug.
- **Background delivery:** When run in background mode, the notification fires as soon as the subprocess exits. The agent must then read the vault file, verify completeness, generate the LLM summary, patch the vault, and deliver. Do NOT rely on the notification's raw output.

```python
# SAFE — append-only, can't corrupt:
def add_link(vault_path, link_name):
    """Append a [[wikilink]] to the Conexiones section. Never reads full file."""
    # Check if link already exists (tail read, 500 bytes max)
    with open(vault_path, "rb") as f:
        f.seek(-min(500, os.path.getsize(vault_path)), os.SEEK_END)
        tail = f.read().decode()
    if link_name in tail:
        return  # already linked
    
    # Check if Conexiones section exists (tail read)
    if "## Conexiones" in tail:
        # Append to existing section
        with open(vault_path, "a") as f:
            f.write(f"- [[{link_name}]]\n")
    else:
        # Create new section at end
        with open(vault_path, "a") as f:
            f.write(f"\n\n## Conexiones\n\n- [[{link_name}]]\n")
```

**Guardrails:**
- Skip files < 200 bytes (already corrupted, don't touch)
- Only read last 500 bytes — never open full file
- Only use append mode (`"a"`) — never write mode (`"w"`)
- Verify transcript section still exists after write (sanity check)
- **Never** use `read_file()` or `write_file()` from hermes_tools for vault edits

## Output Formats (alternative)

When the user explicitly asks for a specific format (not just the pipeline):

- **Chapters**: Group by topic shifts, output timestamped chapter list
- **Summary**: Concise 5-10 sentence overview of the entire video
- **Chapter summaries**: Chapters with a short paragraph summary for each
- **Thread**: Twitter/X thread format — numbered posts, each under 280 chars
- **Blog post**: Full article with title, sections, and key takeaways
- **Quotes**: Notable quotes with timestamps

## Related Skills

- `transcription-pipeline` — For local media files (not YouTube). Full 10-phase pipeline with audio extraction, chunking, and ASR.
- `obsidian` — Vault management. Load this when saving transcripts to the vault, to list/search existing notes.
- `task-decomposition` — For breaking complex video analysis into parallel subtasks.
- `subagent-driven-development` — Orchestration pattern: multi-phase dispatch with parallel waves and per-subagent TDD.

## Skill Contents

| File | Purpose |
|------|---------|
| `SKILL.md` | Main pipeline and usage documentation |
| `scripts/pipeline.py` | **Unified pipeline** — 3-strategy cascade, QC, cleanup, JSON output (canonical) |
| `scripts/fetch_transcript.py` | Legacy: YouTube transcript API only (no fallback, no cleanup) |
| `scripts/cleanup_transcript.py` | Legacy: group raw segments into ~30s blocks (pipeline.py does this inline) |
| `scripts/interlink.py` | Vault interlinking helper |
| `templates/vault_note.md` | Obsidian vault note structure (with `video_id` + `slug` frontmatter) |
| `references/output-formats.md` | Output format examples (chapters, threads, etc.) |
| `references/youtube_qc_guide.md` | QC expectations for YouTube auto-captions |
| `references/code-first-benchmark.md` | Benchmark data: why sequential code beats subagents (90x faster) |
| `references/youtube-title-retrieval.md` | How to get video titles (scrape `<title>` or oEmbed) |
| `references/whisper-fallback.md` | Whisper model selection, channel routing, timeout guidance |

## Error Handling

- **Transcript disabled / API failure:** `scripts/pipeline.py` maneja automáticamente la cascada: si E1 falla → E3 (whisper chunked 10 min) → si E3 falla → E2 (whisper whole file). No requiere intervención del agente.
- **Private/unavailable video**: relay the error and ask the user to verify the URL.
- **No matching language**: discover available languages with `api.list(id)` and report them. If translatable to a language the user wants, use the source-language fetch and note the available translation.
- **Dependency missing**: run `pip install youtube-transcript-api yt-dlp` for primary + fallback; `faster-whisper` is pre-installed in the venv.
