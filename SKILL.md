# Transcription Pipeline — Agent Workflow

This document describes the complete agent workflow for fetching YouTube transcripts, processing them, generating summaries, saving to an Obsidian vault, and delivering results.

## Pipeline Script

`scripts/run.py` is the unified script implementing 3 cascading strategies:

```
URL → E1: YouTube Transcript API (fast, gold standard)
    → E3: yt-dlp + faster-whisper CHUNKED (10 min chunks, model loaded once)
    → E2: yt-dlp + faster-whisper WHOLE FILE (last resort)
    → QC + cleanup (~30s blocks)
    → Delete downloaded audio
    → JSON to stdout: title, video_id, slug, duration, lang, method, qc, blocks, raw_segments, clean_transcript
```

**E3 preferred over E2** because:
- Chunked loads the whisper model once for all chunks (more efficient)
- Better memory handling on long videos
- If a chunk fails, resume from the next one (not everything lost)

**Audio always deleted** after transcription. No residue left.

```bash
python3 scripts/run.py "https://youtube.com/watch?v=VIDEO_ID"
```

The script outputs JSON. The agent (LLM) then reads that JSON, generates an abstractive summary, writes the vault note, and delivers only the summary.

## Full Pipeline — Code-First Execution

### Phase 1: Fetch

**Always discover available languages first** before attempting fetch. The script does this automatically:

1. Tries YouTube Transcript API (E1) with language discovery
2. On TranscriptsDisabled → falls back to E3 (chunked whisper)
3. On E3 failure → falls back to E2 (whole file)

The script fetches the video title automatically via `yt-dlp --print title`, falling back to HTML `<title>` scraping.

### Phase 2: Quality Control (QC)

Run inline — no external tools:

- **Coverage**: Verify segments cover ≥90% of total duration
- **Chronological order**: Verify `t[i].start <= t[i+1].start` for all i
- **Integrity**: Verify enough segments for duration (≥50% of expected)

### Phase 3: Cleanup (~30s blocks)

The pipeline script handles cleanup inline automatically — groups blocks every ~25s of silence gap, wraps text at 80 chars, and marks each block with `### [MM:SS]`.

Each block gets a timestamp marker. The full text is also kept raw in JSON.

### Phase 4: Summary

Generate a summary with these constraints:

| Constraint | Value |
|-----------|-------|
| Max length | **Tiered by duration**: ≤20 min → 300 chars, 20-40 min → 450 chars, 40-60 min → 800 chars, >60 min → 1000 chars |
| Content | Cover core thesis, key facts, numbers, practical takeaways |
| Format | Plain text — no bold, no headers, no emoji, no bullets |
| Self-contained | Readable without watching the video |

### Phase 5: Vault Save

If an Obsidian vault is configured, save a structured note:

```markdown
---
fecha: <date>
fuente: YouTube
url: <video_url>
video_id: <yt_id>
slug: <slug>
duracion: <MM:SS>
idioma: <language>
metodo: <E1/E3/E2>
tags: [transcripcion, youtube, <relevant-tags>]
---

# <Title>

**Fuente:** <url>
**Duración:** <duration>
**Idioma:** <language>

## Resumen Unificado

<summary>

## Transcript Clean

<cleaned transcript with ### [MM:SS] blocks>

## Technical Metadata

- Raw segments: <N>
- Clean blocks: <N>
- Total duration: <duration>
- Coverage: <N>%
- Cleanup: ~30s blocks, normalized punctuation
- Quality control: PASS
```

Path: `$VAULT/Transcripciones/YouTube/<video_id>_<slug>.md`

**Slug generation:** From video title. Lowercase, spaces→hyphens, strip special chars (keep a-z, 0-9, hyphens, accented chars). Max 60 chars.

The vault note MUST include the full cleaned transcript, not just the summary.

### Phase 6: Delivery

Deliver **exactly one message** containing ONLY the summary text. No QC report, no timing, no vault path, no pipeline metadata, no "offer to refine". The user sees only the summary.

## Execution Mode: Code-First

**Rule:** Everything that can be done via code, MUST be done via code. Only use LLM reasoning for tasks that genuinely need it (summary generation, semantic decisions).

**Zero-verbosity rule:** The user sees ONLY the summary. No tool call output, no progress messages, no QC reports, no vault paths. The pipeline runs silently.

**Two-phase execution:**
1. Script does all mechanical work (fetch, fallback, cleanup, JSON output) → outputs structured data to stdout
2. Agent reads the structured data, generates a proper LLM summary (not extractive), saves vault note, and delivers ONLY the summary text

**Never deliver an extractive summary** (sampled blocks from middle of transcript). The user expects a coherent, abstractive summary.

**Deliverable:** One single message — ONLY the summary. Nothing else. No QC report, no timing, no vault path, no pipeline metadata. Just the plain text of the summary.

### One-Call Rule

No fragmentation across tools. One Python script, one commit to the conversation. If a dependency needs installing, do it as a silent terminal call before the script, not interleaved in the pipeline.

## Fallback Notes (Whisper)

**E3 (chunked) preference over E2 (whole):**
- `youtube-transcript-api` → fast, preferred. On `TranscriptsDisabled`, catch the exception and switch to whisper.
- E3 first: split audio into 10 min chunks and transcribe each with the same model (loaded once). Preferred over E2 because it handles memory better, is more resilient, and loads the model once for all chunks.
- E2 last resort: full transcription in one pass if chunking fails.
- `yt-dlp -x --audio-format mp3` → download audio. Output template: `%(id)s.%(ext)s`.
- `faster-whisper.WhisperModel("tiny", device="cpu", compute_type="int8")` → CPU whisper, 5× faster than `base`. Adequate for Spanish/voice.
- Model loads in ~2s.
- Set `timeout=1800` for whisper subprocess. For videos >30 min, use background execution.
- `scripts/run.py` implements all cascade logic, QC, cleanup, and JSON output automatically.
- **Audio always deleted** after successful transcription. The pipeline leaves no temp files.

## Post-Delivery: Vault Interlinking

After delivering the summary, scan ALL existing vault transcripts and add `[[wikilinks]]` to related ones.

**Link rules:**
- Shared agents/themes → link
- Shared models/tools → link
- Shared topics → link
- Add links in both directions where relevant
- Wikilinks go at the end of the file under a `## Conexiones` section

**CRITICAL: APPEND-ONLY pattern.** Never read the whole file and rewrite it. Only append.

## Pitfalls

- **E3 before E2:** Always try chunked whisper (10 min) before whole-file. Chunking loads the model once, handles memory better.
- **yt-dlp output template (CRITICAL):** Use a temp directory with `%(id)s.%(ext)s` pattern. DO NOT use `NamedTemporaryFile` with `.mp3` suffix — yt-dlp treats it as a template and produces 0-byte files.
- **PATH for yt-dlp:** yt-dlp lives in `venv/bin/`. Bare `subprocess.run(['yt-dlp', ...])` calls fail with `FileNotFoundError` if venv bin isn't on PATH. Either prepend PATH or use the full path.
- **yt-dlp JS runtime:** Without `--extractor-args youtube:js_es=deno`, yt-dlp can fail silently on extraction. Always include this flag.
- **Whisper Segment API:** `faster_whisper` segments do NOT have `.duration`. Use `segment.end - segment.start`.
- **Audio cleanup:** Always use `shutil.rmtree()` on the parent temp directory, not `os.remove()` on the file. The file sits in a dedicated temp dir.
- **Language detection:** Capture `info.language` from `segs, info = model.transcribe(...)`. Language is NOT in the segment objects.
- **stdout buffering:** In scripts run as background processes, stdout needs `sys.stdout.reconfigure(line_buffering=True)` to flush output in real time.

### Safe Interlinking (append-only)

```python
def add_link(vault_path, link_name):
    \"\"\"Append a [[wikilink]] to the Conexiones section. Never reads full file.\"\"\"
    with open(vault_path, "rb") as f:
        f.seek(-min(500, os.path.getsize(vault_path)), os.SEEK_END)
        tail = f.read().decode()
    if link_name in tail:
        return  # already linked

    if "## Conexiones" in tail:
        with open(vault_path, "a") as f:
            f.write(f"- [[{link_name}]]\\n")
    else:
        with open(vault_path, "a") as f:
            f.write(f"\\n\\n## Conexiones\\n\\n- [[{link_name}]]\\n")
```

**Guardrails:**
- Skip files < 200 bytes (corrupted, don't touch)
- Only read last 500 bytes — never open full file
- Only use append mode (`"a"`) — never write mode (`"w"`)
- **Never** use `read_file()` → `write_file()` round-trip for vault edits (adds line numbers)

## Error Handling

- **Transcript disabled / API failure:** `scripts/run.py` handles cascade automatically: E1 fails → E3 (chunked whisper) → E2 (whole file). No user intervention needed.
- **Private/unavailable video:** relay the error and ask the user to verify the URL.
- **Dependency missing:** install `youtube-transcript-api`, `yt-dlp`, and `faster-whisper` from requirements.txt.
