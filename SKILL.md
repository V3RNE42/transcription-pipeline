---
name: transcription-pipeline
description: "Unified video pipeline: transcribe, summarize, and structure content from ANY video source — YouTube URLs, local media files (mp4/mkv/mp3/wav), live streams, and embedded videos. Handles both API-based transcript fetch and local ASR (faster-whisper). Absorbs the former youtube-content skill."
version: 2.5.0
author: Hermes Agent
license: MIT
aliases: [video-pipeline, youtube-pipeline, transcription-pipeline]
metadata:
  hermes:
    tags: [transcription, audio, video, pipeline, whisper, subtitle, srt, youtube]
    related_skills: [audiocraft-audio-generation, audio-transcriber, task-decomposition, subagent-driven-development]
---

# Transcription Pipeline — Mandatory Pipeline (Never Skip Steps)

> ⚠️ **THIS IS A MANDATORY PIPELINE.** Every step below must be followed in order for every video/audio transcription task. No skipping phases, no shortcuts, no creative interpretation of delivery format. If you catch yourself about to send anything to chat that is not the tiered resumen unificado (plain text only), STOP — you are violating the skill.

## TL;DR — The Golden Rules

1. **Chat delivery = ONLY the resumen unificado.** Plain text, max length per duration tier, no headers/bold/emoji/bullets/lists of any kind. The summary is the ONLY text sent to chat.
2. **Vault = everything else.** Full structured transcript with `### [MM:SS]` blocks, metadata frontmatter, QC data.
3. **Every phase runs.** No skipping. If you think "this step isn't needed," run it anyway.
4. **Summary length is tiered by video duration.** Measured via `len(summary)` in code before delivery. If over the cap, truncate and verify again.
5. **Summary language = original content language.** Never default to English or translate unless the user explicitly requests it.

## Overview

End-to-end audiovisual transcription pipeline that ingests media files (audio/video), inspects technical metadata, prepares and chunks audio deterministically, transcribes per-unit with retry logic, reconstructs chronologically, and delivers structured outputs (markdown, JSON, SRT, VTT, plain text, manifest). Designed for long-form content (podcasts, lectures, meetings, documentaries) with principled fault tolerance.

## When to Use

- User provides a media file (`.mp4`, `.mkv`, `.webm`, `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.amr`, `.opus`, `.aac`, `.wma` — **or any format FFmpeg can decode**) and asks for a transcript
- User wants subtitles (SRT/VTT) generated from a video
- User needs structured output (timestamps, segments, speaker labels) from a recording
- User needs to process long-form content (>30 min) that requires chunking
- User wants a complete transcript pipeline with quality control, not just a quick dump

**Do NOT use for:**
- Audio generation tasks (use `audiocraft-audio-generation`)
- Quick one-off text extraction from a short clip (<5 min with no chunking needed — simpler approaches work)

## Principles (The Four Pillars)

| Principle | Meaning |
|-----------|---------|
| **Integrity** | Every segment of source audio is transcribed exactly once. No gaps, no duplicates. |
| **Traceability** | Every output line maps back to a source time range, a chunk file, and a transcription run. |
| **Idempotence** | Same input + same parameters = identical outputs. Chunk boundaries are deterministic. |
| **Controlled degradation** | Failed segments produce structured gap markers, not silent corruption. Partial results are always deliverable. |

## Inputs and Outputs

### Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | string | **required** | Path or URL to the media file |
| `language_hint` | string | `auto` | Language code (`en`, `es`, `fr`, etc.) or `auto` for auto-detect |
| `output_formats` | array | `[markdown, json]` | Output formats to generate |
| `chunking_policy` | string | `auto` | `auto`, `fixed_duration`, or `no_chunking` |
| `target_chunk_duration_sec` | int | `600` | Target duration per chunk in seconds (default: 10 min) |
| `chunk_overlap_sec` | float | `5.0` | Overlap between adjacent chunks in seconds |
| `speaker_handling` | string | `basic` | `none`, `basic` (diarization-adjacent heuristics), or `diarization` (requires external model) |
| `cleanup_level` | string | `standard` | `minimal`, `standard`, or `aggressive` (removes filler words, false starts) |
| `retain_intermediates` | bool | `true` | Keep intermediate files (audio/, chunks/, transcripts/) after pipeline completes |

### Output Files

| File | Required? | Description |
|------|-----------|-------------|
| `transcript_final.md` | **Yes** | Formatted markdown transcript with timestamps and structure |
| `transcript_final.txt` | No | Plain text transcript (no timestamps) |
| `transcript_segments.json` | **Yes** | Per-segment JSON with start_sec, end_sec, text, confidence, chunk_src |
| `subtitles.srt` | Conditional | SRT subtitle file (generated when srt in output_formats or source is video) |
| `subtitles.vtt` | Conditional | WebVTT subtitle file (generated when vtt in output_formats) |
| `manifest.json` | **Yes** | Full pipeline manifest: parameters, job metadata, file map, warnings, quality score |
| `logs/pipeline.log` | **Yes** | Structured log of every pipeline operation with timestamps |

## Pipeline Directory Structure

Every job creates an isolated workspace under the current working directory:

```
transcription_job_<job_id>/
├── input/              # Original source file (symlink or copy)
├── audio/              # Extracted/prepared audio (.wav)
├── chunks/             # Chunked audio segments (.wav)
├── transcripts/        # Per-chunk raw transcription outputs (.json, .txt)
├── outputs/            # Final assembled outputs
│   ├── transcript_final.md
│   ├── transcript_final.txt
│   ├── transcript_segments.json
│   ├── subtitles.srt
│   ├── subtitles.vtt
│   └── manifest.json
├── tmp/                # Scratch space for intermediate operations
└── logs/
    └── pipeline.log    # Structured pipeline log
```

## The 11-Phase Pipeline

Each phase is a distinct stage with clear entry criteria, operations, and exit criteria.

### Phase 1: Ingestion and Validation

**Goal:** Accept the source, verify it exists, determine its type, create the job workspace.

**Operations:**
1. Generate `job_id` from timestamp + 6-char hex hash of source path
2. Create directory tree: `transcription_job_<id>/` with all subdirectories
3. Copy or symlink source into `input/` preserving original filename
4. Validate file exists, is non-empty, and has a recognized extension
5. Log source checksum (SHA256) to `manifest.json`
6. Initialize `logs/pipeline.log`

**Exit criteria:** Source verified, workspace ready, manifest initialized.

### Phase 2: Technical Inspection

**Goal:** Inspect the media file to determine format, codec, duration, sample rate, channels, and suitability.

**Operations:**
1. Run `inspect_media.py` on the source file
2. Extract: duration_sec, sample_rate_hz, codec, bitrate, channels, width/height (if video)
3. Validate: duration > 0, sample rate ≥ 8000 Hz, at least 1 audio channel
4. Record all metadata in `manifest.json` under `technical_metadata`

**Exit criteria:** Technical metadata collected and validated; file is fit for processing.

### Phase 3: Audio Preparation

**Goal:** Extract and normalize the audio track to a consistent format for transcription.

**Operations:**
1. Run `prepare_audio.py` on the source
2. Output: single `audio/audio_prepared.wav` file
3. Normalization: 16-bit PCM, 16,000 Hz mono (Whisper-optimal), loudness normalization (EBU R128)
4. If source has video, extract audio track only; if already audio, convert if needed
5. Skip if already in optimal format (idempotency check)
6. Verify output has a non-zero duration and valid WAV header

**Exit criteria:** Clean 16kHz mono WAV ready for chunking.

### Phase 4: Chunking Decision

**Goal:** Decide whether and how to chunk based on duration, policy, and content type.

**Operations:**
1. Compare total duration against `target_chunk_duration_sec`
2. Decision table:
   - Duration ≤ `target_chunk_duration_sec` × 0.8 → **no chunking** (single pass)
   - Duration > `target_chunk_duration_sec` × 0.8 → **chunk**
3. If `chunking_policy = no_chunking` → skip chunking regardless
4. If `chunking_policy = fixed_duration` → always chunk using `target_chunk_duration_sec`
5. Log the decision and parameters in `manifest.json`

**Exit criteria:** Chunking decision made and recorded.

### Phase 5: Integrated Video/Audio Chunking

**Goal:** Split the prepared audio into deterministic, overlapping chunks.

**Operations:**
1. Run `chunk_audio.py` with parameters: `input`, `chunk_duration`, `overlap`, `output_dir`
2. Chunk files written to `chunks/` with deterministic naming

### Chunk Naming Convention (C1-C10)

Chunk files follow a deterministic, self-describing naming scheme:

**Format:** `chunk_NNNN_TTT_BBB.wav`

Where:
- `NNNN` = Zero-padded chunk index (0001, 0002, …)
- `TTT` = Start timestamp in `HH-MM-SS` format
- `BBB` = End timestamp in `HH-MM-SS` format

**Example:** `chunk_0003_00-20-00_00-30-00.wav`

**Rules (C1–C10):**

| Rule | Description |
|------|-------------|
| C1 | Chunks are numbered sequentially starting at 0001 |
| C2 | Each chunk is exactly `target_chunk_duration_sec` long (last chunk may be shorter) |
| C3 | Overlap is exactly `chunk_overlap_sec` seconds — chunk N's last overlap_sec seconds overlap with chunk N+1's first overlap_sec seconds |
| C4 | First chunk starts at 00:00:00. No zero padding before the first segment. |
| C5 | Chunk naming is deterministic: identical input + identical parameters = identical filenames |
| C6 | Timestamps in filenames are relative to source start, rendered as `HH-MM-SS` |
| C7 | Last chunk may be shorter: its end timestamp equals the total source duration |
| C8 | Chunk 0001 always starts at `00-00-00` |
| C9 | Overlap regions are preserved in both chunks — deduplication happens during reconstruction |
| C10 | If `no_chunking` was decided, a single chunk file `chunk_0001_00-00-00_<end>.wav` is created as a symlink to the prepared audio |

**Exit criteria:** All chunk WAV files exist in `chunks/` with valid headers and correct durations.

### Phase 6: Per-Unit Transcription

**Goal:** Transcribe each chunk independently using a speech-to-text engine.

**Operations:**
1. Run `transcribe_units.py` over all chunk files in `chunks/`
2. Each chunk produces a raw transcription in `transcripts/chunk_NNNN_raw.json`
3. Raw output includes: per-word timestamps (relative to chunk start), confidence scores, detected language
4. If `speaker_handling = basic`, apply heuristic speaker segmentation (pause thresholds, pitch tracking)
6. Default STT engine: faster-whisper (local, CPU, `tiny` model). Configurable via environment variable `TRANSCRIPTION_ENGINE`. Log per-chunk duration, word count, confidence statistics.
6. Log per-chunk duration, word count, confidence statistics

**Exit criteria:** Every chunk has a raw transcript file in `transcripts/`.

### Phase 7: Retries and Fault Tolerance

**Goal:** Handle transcription failures gracefully with escalating retry strategy.

**Operations:**
1. Check each chunk's output. If missing or confidence < 0.3 → failed.
2. Retry strategy (3 attempts per chunk):

| Attempt | Action |
|---------|--------|
| 1st (identical) | Re-transcribe the identical chunk file. Log as `retry_1`. |
| 2nd (smaller) | Split the failed chunk in half (halve duration, remove overlap). Retry each half. Log as `retry_2`. |
| 3rd (cleanup) | Apply aggressive noise reduction and re-encode at lower quality. Retry. Log as `retry_3`. |

3. If all 3 attempts fail for a sub-segment → mark as `failed_final`
4. Failed segments get a gap marker in reconstruction: `[MISSING TRANSCRIPTION: chunk_NNNN from TT:BB to TT:BB]`
5. Track all retries and failures in `manifest.json` under `retries` and `failures`

**Exit criteria:** Every chunk either has a valid transcript or a structured failure marker.

### Phase 8: Chronological Reconstruction

**Goal:** Merge all per-chunk transcripts into a single, chronologically sorted, deduplicated transcript.

**Operations:**
1. Load per-chunk transcripts. Convert per-word **chunk-relative** timestamps to **source-relative** timestamps by adding chunk start offset.
2. Sort all words/segments by source-relative start time
3. Eliminate overlap duplicates: for segments in the overlap zone, keep the one with higher confidence or earlier timestamp. Log deduplications.
4. Merge adjacent segments where gap < 0.3 seconds (configurable via `MERGE_GAP_THRESHOLD`)
5. Re-number segments sequentially

**Tools used:**
- `recompose_transcript.py` handles the full reconstruction logic

**Exit criteria:** Single, sorted, gap-free (except failures) segment list with source-relative timestamps.

### Phase 9: Cleanup, Structuring, and Minimal Enrichment

**Goal:** Remove artifacts, structure the transcript into readable blocks, add metadata, apply cleanup level.

**Operations:**
1. Apply `cleanup_level`:
   - `minimal`: No cleanup. Raw text as-is.
   - `standard` (default): Group raw segments into ~30s blocks with `### [MM:SS]` timestamp headers. Normalize punctuation (remove spaces before punctuation, single spaces between words). Wrap text at ~80 chars for readability.
   - `aggressive`: Standard + remove filler words (um, uh, like), false starts, repeated punctuation. Collapse long pauses (>5s) into `[pause Ns]`.
2. Structure the markdown output:
   - Metadata header with source, duration, language, method, quality
   - Timestamp markers every ~30s as `### [MM:SS]`
   - Clean block text underneath each marker
   - Blank line separators between blocks
3. Apply minimal enrichment: capitalisation, sentence boundary detection, paragraph breaks
4. Generate all output files in `outputs/`

**Tools used:**
- `render_outputs.py` generates all output formats
- For YouTube sources, cleanup runs on the API payload directly (no ffmpeg/whisper needed)

**Exit criteria:** All requested output formats written to `outputs/`, text cleaned and structured into temporal blocks.

### Phase 10: Quality Control

**Goal:** Score the output transcript for quality, detect anomalies, and report warnings.

**Operations:**
1. Run quality checks (see below)
2. Calculate aggregate quality score (0.0–1.0)
3. Record quality report in `manifest.json`
4. If score < 0.5, add a `quality_warning` to `manifest.json`

### Quality Checks (QC1–QC8)

| Check | Description | Threshold |
|-------|-------------|-----------|
| QC1 Coverage ratio | (Transcribed duration) / (Total duration) | ≥ 0.95 |
| QC2 Word confidence | Mean confidence across all segments | ≥ 0.60 |
| QC3 Gap detection | Total gap duration / Total duration | ≤ 0.05 |
| QC4 Duplicate text | Percentage of duplicate adjacent segments | ≤ 0.02 |
| QC5 Language consistency | Percentage of segments in expected language | ≥ 0.90 |
| QC6 Timestamp monotonicity | All start_sec values are strictly increasing | Pass/fail |
| QC7 Min segment duration | No segment shorter than 0.5s (except sentence fragments) | ≥ 99% of segments |
| QC8 Output completeness | All requested output files exist and are non-empty | Pass/fail |

**Exit criteria:** Quality score calculated, manifest finalised, warnings if any.

### Phase 11: Transcript Sanity Check

**Goal:** Validate that the generated transcript has proportional content for the video duration. Prevents delivery of chapter summaries, outlines, or placeholders mistaken for full transcripts.

**Operations:**
1. Ensure the vault file has `duration: Ns` in its YAML frontmatter (set during vault write step).
2. Run `python3 scripts/transcript_sanity.py <vault_file.md>` on the saved vault file.
3. Parse the exit code:
   - **0 (PASS):** Continue to delivery. Log `sanity_check: passed` in manifest.
   - **1 (FAIL):** The file is too sparse (< min_wpm threshold). Do NOT deliver. Log `sanity_check: failed` in manifest. Re-run the pipeline from Phase 8 with full dedup enabled. If the full pipeline was already used (e.g., SRT pipeline for a 7h video that timed out during dedup), this means the chapter-based fallback was applied — inform the user that the video is too long for a verbatim transcript and offer the chapter summary instead.
   - **2 (WARN):** Below comfortable threshold but above absolute minimum. Log `sanity_check: warn` in manifest. Continue to delivery but flag the warning.
   - **3 (ERROR):** Could not read file or parse duration. Log `sanity_check: error` in manifest. Continue to delivery (the error might be a parsing issue, not a content problem).

### WPM Thresholds (age-adjusted by duration)

| Duration | Min floor (FAIL) | Warn threshold | Rationale |
|----------|-----------------|----------------|-----------|
| < 10 min | 50 wpm | 90 wpm | Short videos are dense, no room for silence |
| 10-60 min | 40 wpm | 75 wpm | Medium content has natural pacing |
| > 60 min | 30 wpm | 60 wpm | Long courses/lectures have pauses, demonstrations, repetition |

**Example:** A 458-minute course with 630 words → 1.4 wpm → **4.6% of minimum** → FAIL. Catches chapter-summary-instead-of-transcript exactly.

**Exit criteria:** Sanity check result logged in manifest. FAIL status triggers pipeline re-run or user notification.

### Phase 12: Delivery — ⚠️ MANDATORY FORMAT

**Code-first rule:** All mechanical work (inspection, chunking, transcription, QC, vault write, sanity check) is handled in a single invisible `execute_code` call. No progress messages. No intermediate notifications.

**Chat delivery:** Exactly **one message** containing ONLY the resumen unificado (plain text, ≤1000 chars per tier, no headers/bold/emoji/bullets). Nothing else. No "saved to vault." No QC report. No timing. No vault path. No pipeline metadata. No "here's the summary of what I did." No structural commentary. No "I hope this helps."

**Vault persistence:** Always save a structured note to the vault:
   - Full cleaned transcript with `### [MM:SS]` timestamp blocks
   - Metadata YAML frontmatter (source URL, duration, language, method, quality, tags, yt_id)
   - Path: `$VAULT/Transcripciones/<source_type>/<title>.md`
   - Template: `$VAULT/Plantillas/Transcripcion.md` if it exists

**Post-delivery:** Scan all existing vault transcripts and add `[[wikilinks]]` between related ones (shared topics: agents, models, tools, themes). Wikilinks go under a `## Conexiones` section at the end of each file.

## Error Handling Reference

### Error Classes

| Error | Phase | Action |
|-------|-------|--------|
| Source file not found | 1 | Abort. Print error with resolved path attempts. |
| Unsupported format | 1 | If FFmpeg can decode it (e.g. `.amr`, `.opus`, `.wma`, `.aiff`), log warning and proceed to Phase 3 (FFmpeg handles the conversion). Otherwise abort and list supported formats. |
| Zero-length audio | 3 | Abort. Source may be corrupted. |
| Chunk has no readable audio | 5 | Skip chunk, log warning, tag as failed_final. |
| STT engine not responding | 6 | Retry 3x with 5s backoff, then fail chunk. |
| Out of memory during transcription | 6 | Reduce chunk size by 50%, retry. |
| Disk I/O error | any | Retry 2x, then abort pipeline. |

### Graceful Degradation Path

```
                    ┌───────────────┐
                    │ Source Input  │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Phase 1-3     │
                    │ (Hard fail on │
                    │  corruption)  │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Phase 4-5     │
                    │ (Soft fail —  │
                    │  mark chunk   │
                    │  as failed)   │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Phase 6-10    │
                    │ (Degrade —    │
                    │  gap markers  │
                    │  + warnings)  │
                    └───────┬───────┘
                            │
                    ┌───────▼───────┐
                    │ Delivery      │
                    │ (Always       │
                    │  partial OK)  │
                    └───────────────┘
```

**Rule:** The pipeline never silently drops content. A failed chunk always produces a visible `[MISSING TRANSCRIPTION: ...]` marker in the output.

## Acceptance Criteria

Before considering a transcription job complete, verify:

- [ ] `transcript_final.md` exists and has content
- [ ] `transcript_segments.json` exists with valid JSON
- [ ] `manifest.json` exists with all required fields
- [ ] `logs/pipeline.log` exists with complete operation log
- [ ] All chunk timestamps are monotonic in the final output
- [ ] No silent gaps: any `failed_final` chunk has a `[MISSING TRANSCRIPTION]` marker
- [ ] Quality score ≥ 0.5 (or quality_warning flag present in manifest)
- [ ] **Sanity check passed (P11): words ≥ duration[min] × min_wpm_threshold — prevents summary-instead-of-transcript**
- [ ] Word count is reasonable for the source duration (≥ 1 word per 2 seconds minimum)
- [ ] Output files have consistent segment counts between formats

## Behavior Contract for Natural Language Requests

When the user describes their transcription need in natural language (not as explicit parameters), follow this default mapping:

| User says | Pipeline parameters |
|-----------|-------------------|
| "Transcribe this video" | Defaults: `language_hint=auto`, `output_formats=[markdown,json]`, `chunking_policy=auto`, `speaker_handling=basic`, `cleanup_level=standard` |
| "Get subtitles for this" | `output_formats=[srt,vtt]`, `chunking_policy=auto` |
| "Transcribe this podcast" | `output_formats=[markdown,txt]`, `speaker_handling=basic`, `cleanup_level=aggressive` |
| "Quick transcript, no cleanup" | `output_formats=[txt]`, `cleanup_level=minimal`, `chunking_policy=no_chunking` (if < 30 min) |
| "Full detailed transcript with speakers" | `output_formats=[markdown,json]`, `speaker_handling=basic`, `cleanup_level=minimal`, `retain_intermediates=true` |
| "Translate and transcribe" | Set `language_hint` to requested target language (requires STT engine that supports translation, e.g., Whisper) |
| User provides explicit params (e.g., "chunks of 5 min") | Override the matching parameter from defaults |
| Ambiguous request | Default to `output_formats=[markdown,json]`, ask only about duration if unclear (short < 30 min vs long ≥ 30 min) |

**Golden rule:** When in doubt, use safe defaults. No progress messages, no intermediate notifications. Deliver only the completed result.

### Silent Fallback Protocol (behavioral rule)

When the primary transcript method for a YouTube video fails or returns empty content, **silently proceed with the fallback**. Do NOT ask the user "shall I download and transcribe?" or offer to do it — this is the expected automatic behavior. The fallback chain is automatic:

1. yt-dlp SRT fails or returns empty → try youtube-transcript-api
2. API also fails or returns empty → proceed to audio pipeline (yt-dlp download → chunk → whisper)
3. No user-facing "shall I?" at any step

The only time to inform the user is if ALL fallback methods fail — and even then, state the failure once, don't ask for permission.

**Exception A — MCP extraction failure:** If the MCP `fetch_youtube_transcript` tool returns `[Transcript language: <lang>]` with **empty content** but the player metadata shows `"Subtitles/closed captions (c)"` (captions available), this means auto-generated captions EXIST but the tool couldn't extract them. In this case, go directly to yt-dlp TTML as the next step (`--write-auto-subs --sub-lang <lang_orig>,<lang> --sub-format ttml --skip-download`) — the captions are there, just not via that MCP tool. TTML is preferred over SRT as it avoids triplicate text issues.

**Exception B — Captions truly unavailable:** If the player metadata shows `"Subtitles/closed captions unavailable"` AND the MCP tool returns empty content (or detects language but returns no text), captions do NOT exist — skip yt-dlp SRT and youtube-transcript-api entirely. Go directly to the **full audio pipeline** (yt-dlp download audio → chunk → whisper → vault). Attempting yt-dlp SRT on a video with no captions is a wasted step that fails silently.

### YouTube Integration — Code-First (Default)

For YouTube URLs, use the **code-first sequential pipeline**.

⚠️ **CRITICAL BEHAVIORAL RULES (violations produce user corrections):**\n\n**0. NEVER use mcp_fetch_fetch_youtube_transcript for YouTube transcripts. EVER.** The MCP tool detects that captions exist but may return empty content. This session you violated this rule. Use yt-dlp TTML as the primary method. If you see yourself about to call mcp_fetch_fetch_youtube_transcript for a YouTube URL, STOP — that's a violation.\n\n1. **SILENT FALLBACK IS AUTOMATIC.** Do NOT ask the user "shall I download and transcribe?" or "do you want me to proceed with the audio pipeline?" — this is the expected automatic behavior. If a method fails, silently proceed to the next method in the chain. The user should never see a question about fallback. See Pitfall #23.
3. **CODE-FIRST: All mechanical work is invisible to the user.** Only the final summary text is delivered to chat. No progress messages, no "under the hood" explanations, no intermediate notifications.

**Fallback chain (automatic, no user consultation at any step):**
```
yt-dlp TTML auto-captions (primary) ← MOST ACCURATE, NO TRIPLICATION
  ↓ if empty/fails
yt-dlp SRT (manual subs or converted)
  ↓ if empty/fails
youtube-transcript-api (pip)
  ↓ if empty/fails
Full audio pipeline: yt-dlp download → chunk → whisper → vault
  ↓ if ALL fail
Single failure notification to user (not a question)
```

0. **Early stopper:** Search vault for files containing `yt_id: YT_ID` using `search_files(target='content', pattern='yt_id: <yt_id>', path='$VAULT/Transcripciones')`. If a match exists → read the file, regenerate ONLY the summary (tiered by duration from frontmatter), update the `## Resumen` section in-place, deliver. **Skip all other steps.** If not found, proceed.
   - *Why content search and not filename:* Vault files are saved as `$VAULT/Transcripciones/<source_type>/<title>.md`, with the yt_id only in the YAML frontmatter. A filename pattern `{yt_id}_*.md` would never match.
1. **Fetch via yt-dlp TTML (primary):** Run `yt-dlp --skip-download --write-auto-subs --sub-lang <lang_orig>,<lang> --sub-format ttml --output /tmp/%(id)s <url>`. Use `<lang_orig>` first (e.g., `es-orig`, `en-orig`) for original-language captions, fall back to `<lang>` (e.g., `es`, `en`) for translated auto-captions. TTML is XML and avoids the triplicate-text issue of SRT conversion. Parse with `xml.etree.ElementTree`, extract `<tt:p begin="...">` elements, convert begin timestamps to seconds. See `references/yt-dlp-ttml-pipeline.md` for full recipe.
   - *Fallback: if TTML unavailable, try SRT.*
2. **Parse & deduplicate:** Read SRT with Python `open(fn).read()` (NOT `read_file` — SRT files can exceed 10K lines). Parse segments using **line-by-line SRT parser** (not block-by-block — yt-dlp SRTs have irregular line spacing from `\r\n` normalization). See Auto-Generated Caption Deduplication section for the two-phase approach: (a) filter out segments with duration < 0.2s (the 10ms "echo" fragments), then (b) run `deduplicate_running_text()` with a running-text word-overlap accumulator. Do NOT use the old `deduplicate_adjacent(gap=0.5)` — it collapses all segments into one run when inter-segment gaps are ≤10ms (the common case in auto-generated captions). **Size gate:** After filtering short fragments, check `len(segs)`. If > 5000 segments, the word-overlap dedup will timeout (300s limit). Skip dedup and fall back to the Chapter-Based Extraction approach (see LARGE VIDEO FALLBACK below).
3. **QC:** Inline code checks (coverage ≥98%, monotonic, integrity)
4. **Cleanup:** Group deduplicated segments into ~30s blocks with `### [MM:SS]` markers
5. **Summary:** Agent generates resumen unificado with tiered char limits (the ONLY step requiring LLM)
6. **Vault:** Write structured note to `$VAULT/Transcripciones/YouTube/<title>.md`
7. **Delivery:** One message — ONLY the summary text. No QC, no timing, no vault path.

**Code-first rule:** Everything mechanical goes in a single `execute_code` block (invisible to user). Only the summary text appears in chat. No `delegate_task`. No subagents. **Never** communicate tool execution, code blocks, terminal outputs, or "under the hood" activity to the user — deliver only the final result silently.

**Early stopper rule:** When a video already exists in the vault, do NOT re-fetch or re-run QC/cleanup. Read the existing transcript from the file, regenerate only the summary using the tier threshold that matches the video duration in frontmatter, update `## Resumen`, and deliver. Silent — no "already exists" message to the user. The summary delivered to chat must still follow the mandatory Resumen Unificado format (plain text, tiered length, no headers/bold/emoji/bullets).

Only fall back to the full audio pipeline if YouTube captions are disabled.

**Delivery (non-negotiable):** Exactly one message — ONLY the resumen unificado (plain text, max length per duration tier, no headers/bold/emoji/bullets). See the mandatory Resumen Unificado section for exact limits.

### LARGE VIDEO FALLBACK: Chapter-Based Extraction

When the SRT has >5000 segments after duration filtering (typically videos >3–4 hours), `deduplicate_running_text()` will timeout (300s `execute_code` limit). Switch to this approach:

1. **Download chapters first:** Use `yt-dlp --skip-download --print "%(chapters)s"` to get chapter timestamps. If the video has chapters (most long courses/livestreams do), use them as the structural backbone.
2. **Parse SRT rapidly:** Skip dedup entirely. Rapid-parse segments with a lightweight regex (duration filter only, no word-overlap removal). Accept the mild triplication — it doesn't hurt chapter-level keyword extraction.
3. **Assign to chapters:** For each segment, find which chapter it falls into by timestamp. Accumulate text per chapter.
4. **Extract keywords per chapter:** Use `collections.Counter` with a stopword list to pull out the top 10–15 significant words per chapter. This gives a fingerprint of what each section covers even with raw triplicated text.
5. **Generate chapter summary:** For each chapter, emit `## [MM:SS] Title (duration)` + `Keywords: ...` + first ~350 chars of raw text as preview. This produces a structured course map rather than a full verbatim transcript.
6. **Save to vault** with the chapter summary as the structured transcript body. The `transcript_segments.json` can be omitted — the chapter breakdown IS the structure.

**Trade-off:** You lose clean full-text search and word-level dedup. For 7h+ courses, the structured chapter overview is more useful than a raw triplicated transcript anyway. The user gets a navigable table of contents they can use to jump to specific sections.

**When chapters are unavailable** (rare in structured content, common in vlogs/livestreams): fall back to fixed-duration windows (e.g., every 60s instead of 30s) with keyword extraction per window. Still skip word-overlap dedup — at these scales it's unusable.

### Auto-Generated Caption Deduplication

Auto-generated YouTube captions (especially when fetched via yt-dlp SRT) produce each spoken phrase as **3 consecutive SRT segments**: the phrase, a ~10ms blank segment, and the same phrase repeated. Without deduplication, this triplicates every line in the cleaned transcript.

**⚠️ Known issue with gap-based dedup:** The old `deduplicate_adjacent` (using `min_gap=0.5`) sweeps ALL segments into one run when gaps are continuous (≤10ms between every segment — common in auto-generated YT captions). This keeps only the single longest text, losing almost all content. Use the two-phase approach below instead.

Add this step after parsing segments and before grouping into blocks:

#### Phase 1: Filter short fragments

Auto-generated captions insert 10ms "echo" segments between spoken phrases. Remove them by duration:

```python
segs = [s for s in segments if s["dur"] >= 0.2]
```

Typical reduction for 13-min video: 768 → 384 segments.

#### Phase 2: Running-text word-overlap dedup

After filtering, adjacent long segments still overlap in text (tail of N = head of N+1). Use a rolling accumulator with word-level overlap detection:

```python
import re

def deduplicate_running_text(segs):
    """Remove word-level overlap between adjacent segments using a running-text accumulator."""
    cleaned = []
    running_text = ""
    for s in segs:
        curr_text = s["text"]
        if not running_text:
            running_text = curr_text
            cleaned.append(s)
            continue
        prev_words = running_text.split()
        curr_words = curr_text.split()
        overlap = 0
        for n in range(min(len(prev_words), len(curr_words)) - 1, 1, -1):
            if prev_words[-n:] == curr_words[:n]:
                overlap = n
                break
        if overlap > 0:
            new_words = curr_words[overlap:]
            if new_words:
                new_text = " ".join(new_words)
                running_text = running_text + " " + new_text
                cleaned.append({"start_sec": s["start_sec"], "text": new_text})
        elif curr_text not in running_text:
            running_text = running_text + " " + curr_text
            cleaned.append(s)
    return cleaned, re.sub(r'\s+', ' ', running_text).strip()
```

Typical result: 384 segments → 384 cleaned segments with no duplicate words. Triple compression ratio: ~5.3× semantic compression (768 raw segments → clean content).

**Verification before delivery:** Before emitting the summary to chat, verify that the `yt_id` in the processed output matches the `yt_id` extracted from the user's URL. Never deliver a summary cross-wired from a different video. If early stopper triggered, confirm the matched file's `yt_id` matches the request. Re-running the wrong summary wastes the user's trust.
### Resumen Unificado — ⚠️ MANDATORY (non-negotiable)

**This is the ONLY thing sent to chat.** No exceptions. Full transcripts, timestamps, metadata, QC reports, vault paths, and all other artifacts go exclusively to the vault.

| Constraint | Value |
|-----------|-------|
| Max length | **Tiered by duration** (measured via `len(summary)` in code before delivery): ≤20 min → 300 chars, 20-40 min → 450 chars, 40-60 min → 800 chars, >60 min → 1000 chars |
| Enforcement | `echo "$summary" | python3 scripts/validate_summary.py --duration <dur_sec>` — exits 0 (pass) or 1 (too long, see `excess_chars`). If it fails, **discard the summary and regenerate a shorter one.** Repeat until exit_code=0. Never truncate. |
| Language | Always the original language of the content (detected from the transcript). Never default to English or translate. Only override when the user explicitly requests a specific language. |
| Content | Thesis + key arguments + practical takeaways. Concise, information-dense. |
| Format | **Plain text only.** No headers (`##`, `###`, `---`). No bold (`**`). No italic (`*`). No emoji (🚀, ✅, etc.). No bullets (`-`, `*`, `1.`). No numbered lists. No timestamps (`[MM:SS]`). No line breaks — single paragraph. Pure prose. |
| Message content | ONLY the summary text. No metadata. No vault path. No QC scores. No timing data. No "saved to vault" messages. No structural commentary. Nothing else. |
| Verification | Pipe summary through `python3 scripts/validate_summary.py --duration <sec>`. If `exit_code=1`, discard and **regenerate a shorter summary**. Re-validate. Never truncate. Never send an unvalidated summary to chat. |

**Examples of CORRECT delivery (what the user sees):**
> "This video covers the Hermes Agent Velocity Update, introducing progressive tool loading to reduce context window usage, an agent swarm system for parallel task execution via kanban, model integrations including Qwen 3.7 Max and Opus 4.8, a codebase refactor from 16K to 3.8K lines, an MCP catalog, prompt injection defense, and a rebuilt session search reported as 4500 times faster."

**Examples of WRONG delivery (do NOT do this):**
> Wrong: "## New Features\n- Tool search\n- Agent swarms\n- [00:20] Introduction..." (has headers, bullets, and timestamps)
> Wrong: "Here's the transcript summary saved to vault..." (includes metadata)
> Wrong: "✅ Tool Search ✅ Agent Swarms" (emoji)
> Wrong: A structured breakdown with sections and timestamps (that's a vault artifact, not a summary)

The summary is the **only** text delivered to chat. Full transcript and structural artifacts are saved to the vault for reference.

## Scripts Reference

The pipeline relies on these Python scripts in `SKILL_DIR/scripts/`. Full API documentation (CLI usage, function signatures, return types, fallback chains) is in `SKILL_DIR/references/scripts_api.md` — consult that for detailed integration guidance.

| Script | Phase(s) | Entry Point | Key Dependencies |
|--------|----------|-------------|------------------|
| `inspect_media.py` | P2 | `inspect_media(filepath)` | ffprobe (primary), pure-Python WAV parser (fallback) |
| `prepare_audio.py` | P3 | `prepare_audio(source, workspace)` | ffmpeg (primary), Python `wave` module (fallback) |
| `chunk_audio.py` | P4–P5 | `decide_chunking(media_info)` → `create_audio_chunks(audio)` | ffmpeg, deterministic naming |
| `transcribe_units.py` | P6–P7 | `transcribe_with_retries(unit_path, unit_info, config)` | faster-whisper (default), whisper-cpp or simulated |
| `recompose_transcript.py` | P8 | `recompose(transcripts, chunking_enabled)` | stdlib only |
| `render_outputs.py` | P9–P10 | `render_outputs(final_data, workspace, output_formats)` | stdlib only |
| `transcript_sanity.py` | **P11** | `python3 scripts/transcript_sanity.py <vault_file.md>` | stdlib only (no deps) |
| `validate_summary.py` | **P12** | `echo "$summary" \| python3 scripts/validate_summary.py --duration <sec>` | stdlib only |

## References

| File | What's in it |
|------|-------------|
| `references/source_strategy.md` | Decision guide: YouTube URL → `youtube-content` skill vs local/media file → `transcription-pipeline` |
| `references/manifest_schema.md` | Full JSON schema for `manifest.json` output |
| `references/chunking_policy.md` | Chunking rules C1–C10 and duration recommendations |
| `references/quality_checks.md` | Quality control checks QC1–QC8 with programmatic verification |
| `references/scripts_api.md` | Full script API documentation, CLI usage, and return types |
| `references/cleanup-algorithm.md` | Segment-to-block grouping algorithm (~30s windows) with Python code |
| `references/yt-dlp-ttml-pipeline.md` | TTML pipeline: commands, parsing recipe, language codes (`es-orig`, `en-orig`), verified metrics vs SRT, pitfalls |

## Dependencies

- `ffmpeg` + `ffprobe` (system packages) — audio extraction and inspection
- `faster-whisper` (Python) — speech-to-text via ctranslate2 (CPU, no GPU needed)
- `pydub` (Python) — audio manipulation fallback
- Python 3.9+ with standard library + `json`, `hashlib`, `subprocess`, `pathlib`

### Model Selection

The pipeline uses `faster-whisper` with the **`base`** model by default. Change via config:

```python
config = {
    "engine": "faster-whisper",
    "faster_whisper_model": "base",     # tiny, base, small, medium, large-v3
    "faster_whisper_device": "cpu",
    "faster_whisper_compute": "int8",
}
```

**Why `base` over `tiny`:** `tiny` is fast but produces poor results with AMR 8kHz, accented speech, background noise, or conversational audio. `base` is the lowest reliable model for real-world audio. Use `small` for technical terminology or poor recording quality.

| Model | Load time | Speed (3 min audio) | Accuracy |
|-------|-----------|---------------------|----------|
| `tiny` | ~2s | ~6s | Fast, poor with noisy/conversational audio |
| `base` | ~21s | ~20s | Better for accented/noisy audio ✅ default |
| `small` | ~55s | ~60s | Best accuracy, CPU-bound |
| `medium` | ~2min | ~3min | Overkill for most use cases |

## Common Pitfalls

1. **Skipping phase 4 (chunking decision).** Always check duration before chunking. Chunking a 30-second file is wasteful and can degrade quality.

2. **Fixed chunk durations vs adaptive chunking.** The default chunking policy uses fixed target durations (300s/600s) with large overlaps (10-30s). For faster processing with smaller files, prefer the adaptive approach from `audio-transcriber`: `N = ceil(dur/300)`, `chunk_size = dur/N`, overlap = 3s. This gives chunks of 4-5 min regardless of total duration. Use for audio-only files under 60 min; keep the fixed approach for long videos where larger context chunks help diarization.
2. **Forgetting to convert chunk-relative timestamps to source-relative.** This is the most common reconstruction bug. Phase 8 must add chunk offset to every word timestamp.
3. **Overlap deduplication not removing both copies.** The correct approach: during reconstruction, for the overlap zone, select the higher-confidence source and discard the other. Log the deduplication.
4. **Not checking for empty chunks.** A silent chunk (no speech) should produce an empty transcript with a single `[SILENCE Ns]` marker, not a failure marker.
5. **Hard-failing on partial corruption.** A scratch on the source CD should not lose 30 minutes of content. Degrade gracefully per the error handling table.
6. **Using different STT engines for different chunks.** Stick to one engine per job. Engine switching produces inconsistent timestamps and confidence scores.
7. **Forgetting to set `retain_intermediates`.** Default is `true` so debugging is possible. Set to `false` explicitly for production jobs where disk space matters.
8. **Not cleaning up `tmp/` even when `retain_intermediates = true`.** The `tmp/` directory is always cleaned after Phase 11 — it's scratch space, not intermediate artifacts.
9. **File-existence check before engine dispatch in simulate mode.** `transcribe_units.py` checks file existence only after determining the STT engine. If you call `transcribe_unit` with `engine="simulated"`, it bypasses file-existence checks entirely — no real audio file is needed. A bug introduced by checking file existence at the function entry (before engine dispatch) would break simulation/testing. Always check engine first, then validate file.
10. **Chunking guardrails for short files.** `chunk_audio.py`'s `decide_chunking()` applies two hard guardrails even with `policy="auto"`:
    - Duration < 60s → **never** chunk (single pass is always cheaper)
    - Duration < 1.5× target chunk duration → **never** chunk (the "overhead" of setting up chunking isn't worth it)
    If you bypass `decide_chunking()` and call `create_audio_chunks` directly, you must implement these guardrails yourself or risk chunking tiny files.
11. **Cross-wired video delivery.** Never deliver a summary without verifying the `yt_id` matches. When processing multiple videos in one session, it is easy to hallucinate the wrong summary from memory. Always verify: the `yt_id` in the output title/frontmatter MUST match the `yt_id` extracted from the user URL. If in doubt, re-run the pipeline.
12. **Legacy vault frontmatter format mismatch.** Older vault files may use `duracion: 120:09` (MM:SS) instead of `duration: Xs`. The early stopper must parse both formats: try `duration: (\\d+)s` first, then fall back to `duracion: (\\d+):(\\d+)` (minutes*60 + seconds). Similarly, title may be in `title: "..."` frontmatter or as `# Title` markdown heading. Silent failure to parse returns `duration=0` and empty title, breaking tier calculation.
13. **Whisper Segment API (`faster_whisper`).** Segment objects have `.start` and `.end` properties, NOT `.duration`. Always compute `duration = s.end - s.start`. Using `s.duration` raises `AttributeError`.
14. **VAD filter reduces coverage.** Using `vad_filter=True` in `model.transcribe()` skips significant portions of speech — can reduce coverage from ~99% to ~87%. Omit `vad_filter` entirely for full coverage. Only use it for very noisy recordings where false positives exceed 20% of segments.
16. **yt-dlp output template.** When downloading audio, use `%(id)s.%(ext)s` as output template, NOT a `NamedTemporaryFile` path. yt-dlp treats the `-o` value as a template and writes to expanded paths, not to pre-created files. Using a `NamedTemporaryFile` produces 0-byte output and a `WARNING: unable to obtain file audio codec with ffprobe` error.

17. **SRT from yt-dlp has `\\r\\n` line endings and triplicate text.** When fetching auto-generated captions via `yt-dlp --write-auto-subs --convert-subs srt`, the output SRT uses Windows-style `\\r\\n` line endings even on Linux. Each spoken phrase appears as 3 consecutive SRT blocks: phrase → ~10ms blank → phrase. Always normalize line endings, filter segments by duration (< 0.2s are echo fragments), then run `deduplicate_running_text` for word-overlap removal before block grouping (see the Auto-Generated Caption Deduplication section). Do NOT use the old `deduplicate_adjacent` — it collapses all segments into one run when gaps are ≤10ms (the common case). Prefer `youtube-transcript-api` (as specified in the YouTube Integration section) over yt-dlp SRT; the API returns clean JSON segments with no triplication.

18. **`read_file` truncates large SRT files.** The `read_file` tool defaults to 500 lines. SRT files for long videos (30+ min) are typically 3,000–11,000 lines. Using `read_file` to load an SRT silently returns only the first ~100 segments. Use Python `open(fn).read()` directly. See `references/yt-dlp-srt-pipeline.md` for the full parsing + deduplication recipe.

21. **`deduplicate_running_text` times out on large SRT files (>5K segments).** The word-overlap dedup uses a nested loop that compares every word of every segment against the running accumulator. For short videos (<5K segments, <30 min) this is fast (~1s). For long videos (7h+, 10K+ segments), the same function exceeds the `execute_code` 300s timeout. **Size check before dedup:** if `len(segs) > 5000`, skip `deduplicate_running_text` entirely and use a lighter approach — see "LARGE VIDEO FALLBACK: Chapter-Based Extraction" in the YouTube Integration section below.

19. **Running-text dedup chain breaks when cleaning in-place.** If you remove the overlapping words from the CURRENT segment and then compare the NEXT segment against the CLEANED version, the overlap-detection chain breaks — next segment no longer matches the cleaned text. Always use a running-text ACCUMULATOR (keep the full accumulated text and find overlap against it), not a "clean-and-compare" approach against the previous cleaned segment. See `deduplicate_running_text` in the Auto-Generated Caption Deduplication section.

20. **SRT locale comma decimal separator.** SRT files from non-English YouTube locales (Spanish, French, German, etc.) use commas as decimal separators in timestamps: `00:00:01,709 --> 00:00:03,790` instead of `00:00:01.709 --> 00:00:03.790`. The SRT parser regex must accept both `[.,]` in timestamp patterns (`\\d{2}:\\d{2}:\\d{2}[.,]\\d{3}`), and the `ts_to_sec` function must normalize with `ts.replace(',', '.')`. Calling `float('1,709')` on a comma-separated timestamp raises `ValueError`. The code in the Auto-Generated Caption Deduplication section handles this — but if you write ad-hoc parsing outside that code, you must handle both separators.

22. **Do NOT use MCP fetch tools (mcp_fetch_fetch_youtube_transcript) as the primary method for YouTube transcripts.** The skill specifies yt-dlp TTML as the primary method. MCP fetch tools may detect the transcript language but return empty content for auto-generated captions that actually exist. When that happens, go to yt-dlp TTML next (`--write-auto-subs --sub-lang es-orig,en-orig,es,en --sub-format ttml --skip-download`), not the full audio pipeline. Only fall back to the full audio pipeline (download + chunk + whisper) after both yt-dlp TTML AND youtube-transcript-api have failed.

23. **Silent fallback — do not ask the user for permission.** When a transcript method fails, silently proceed to the next method in the chain. Asking "shall I download and transcribe?" violates the skill's code-first rule (mechanical work is invisible) and wastes the user's time. The transcript pipeline is a deterministic workflow, not a consultative one. If ALL methods fail, state the failure once and only then.

## Verification Checklist

- [ ] Pipeline runs end-to-end on a test file (e.g., 1-min audio clip)
- [ ] All 10 phases complete without unhandled exceptions
- [ ] Output files match requested `output_formats`
- [ ] `manifest.json` contains: job_id, source, parameters, technical_metadata, chunk_list, retries, failures, quality_score, file_map, timestamps
- [ ] Chunk naming follows C1–C10 exactly
- [ ] Timestamps are source-relative and monotonic
- [ ] `[MISSING TRANSCRIPTION: ...]` markers present for any failed segments
- [ ] Quality score calculated and ≥ 0.5 (or warning present)
- [ ] **P11 sanity check: vault file passes `transcript_sanity.py` (≥ min wpm threshold)**
- [ ] Re-running with the same source + parameters produces identical output (idempotence test)
