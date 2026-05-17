# Transcription Pipeline

Code-first YouTube transcription pipeline with 3 cascading strategies, QC, cleanup, LLM summary, vault integration, and interlinking. No subagent overhead.

**Philosophy:** Everything that can be done via code, MUST be done via code. Only LLM reasoning for summaries. No progress spam, no metadata in delivery — just the result.

## Quick Start

```bash
pip install -r requirements.txt

# Process any YouTube URL — auto-detects strategy
python scripts/run.py "https://youtu.be/VIDEO_ID"
```

The script outputs JSON to stdout with all pipeline data. Sample:

```json
{
  "title": "Video Title",
  "video_id": "abc123def45",
  "slug": "video-title",
  "duration": "11:12",
  "lang": "en",
  "method": "youtube-api",
  "segments": 289,
  "blocks": 27,
  "transcript": "### [00:00]\\n\\nFull transcript with blocks...",
  "qc": { "coverage": 100.0, "chronological": true }
}
```

## Pipeline — 3 Cascading Strategies

```
URL
 │
 ├─ E1: YouTube Transcript API  ← fast, gold standard
 │    (instant, if captions exist)
 │
 ├─ E3: yt-dlp + whisper CHUNKED  ← preferred fallback
 │    (10 min chunks, model loaded once, memory-efficient)
 │
 └─ E2: yt-dlp + whisper WHOLE FILE  ← last resort
      (single pass, full audio in memory)
```

| Strategy | When | Requires |
|----------|------|----------|
| E1 - YouTube API | Video has captions/subtitles | `youtube-transcript-api` |
| E3 - Whisper chunked | No captions, long video | `yt-dlp` + `faster-whisper` + `ffmpeg` |
| E2 - Whisper whole | No captions, E3 fails | `yt-dlp` + `faster-whisper` + `ffmpeg` |

**E3 is preferred over E2** because:
- Chunks of 10 min — better memory management
- Whisper model loaded once for all chunks
- If a chunk fails, retry from next (not everything lost)
- **Audio file is always deleted** after transcription — zero residue

### Strategy Selection

```python
# Automatic cascade — script tries E1, then E3, then E2
python scripts/run.py "https://youtu.be/VIDEO_ID"

# Title extraction uses yt-dlp --print title, 
# falls back to HTML <title> scraping
```

## Language Detection

- **E1:** Language from YouTube API metadata
- **E3/E2:** Language auto-detected by Whisper (via `info.language`)

Output in `"lang"` field of JSON.

## QC Checks

| Check | Description | Pass threshold |
|-------|-------------|----------------|
| Coverage | % of total duration covered by segments | ≥90% |
| Chronological | Segments in order (t[i].start ≤ t[i+1].start) | Required |
| Integrity | Enough segments for duration | ≥50% of expected |

Run inline in the script — zero subprocess overhead.

## Cleanup: ~30s Transcript Blocks

Raw YouTube API output is one segment per subtitle line (~2-5s each). The cleanup phase:

1. Groups sequential segments into **~30s blocks**
2. Each block gets a `### [MM:SS]` marker
3. Text is wrapped at 80 chars for readability
4. Raw segments are preserved in `"raw_segments"` array

```markdown
### [00:00]
MiniMax M2.7 just changed AI agents forever. And most people have no idea this
even happened. Here's what's wild. MiniMax took their new AI model. They gave it
a job. The job was to make itself better.

### [00:26]
That improves itself with almost no human help. This is MiniMax M2.7. And if
you're sitting here thinking AI agents are just chatbots...
```

## Summary Generation (LLM Phase)

The script outputs raw structured JSON. The agent (LLM) then:

1. Reads the JSON (title, duration, blocks, transcript)
2. Generates an **abstractive** summary (not extractive sampling)
3. Saves full vault note with transcript + summary
4. Interlinks related vault entries

**Summary length tiers** (by video duration):

| Duration | Max chars |
|----------|-----------|
| ≤20 min | 300 |
| 20-40 min | 450 |
| 40-60 min | 800 |
| >60 min | 1000 |

## Benchmark

Processing a 23:54 video with YouTube captions:

| Approach | Time | Subagents |
|----------|------|-----------|
| Subagent wave (5 delegate_task) | **~6 min** | 5 |
| Code-first sequential (API only) | **~4 s** | **0** |
| Code-first with whisper fallback | **~30 s** | **0** |

**Code-first is ~90× faster** for procedural tasks. Subagent overhead dwarfs compute.

Even with whisper fallback (model load + transcription), the pipeline finishes in seconds for short videos and under a minute for most content.

## Observability

The script prints JSON to stdout. The agent (or any consumer) reads this JSON:

```python
import json, subprocess

result = subprocess.run(
    ["python", "scripts/run.py", "https://youtu.be/VIDEO_ID"],
    capture_output=True, text=True, timeout=600
)
data = json.loads(result.stdout)
print(data["title"], data["duration"], data["lang"])
print(data["qc"]["coverage"])  # 100.0
```

No files written to /tmp/ unless you pipe stdout yourself.

## Requirements

```
youtube-transcript-api>=2.0.0   # E1: YouTube API
yt-dlp>=2025.0                  # E3/E2: audio download
faster-whisper>=1.0.0           # E3/E2: speech-to-text
ffmpeg                          # E3/E2: audio processing (system package)
```

Install system deps:

```bash
# Linux
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows (Chocolatey)
choco install ffmpeg
```

## Vault Integration

Target path: `~/vault/Transcripciones/YouTube/<video_id>_<slug>.md`

After delivery, the agent scans all existing vault notes and adds `[[wikilinks]]` between related ones — automatic knowledge graph building. Files use an append-only pattern (never read/write round-trip) to prevent corruption.

## Delivery Format

**Only the summary text.** No metadata, no QC report, no timing, no vault path, no progress messages. Just the content, delivered in the user's language.

## Agent Integration

The pipeline is designed for agent-in-the-loop execution:

1. **Script** does all mechanical work (fetch, QC, cleanup, vault save, interlinking)
2. **Agent** reads JSON output, generates LLM summary, patches vault, delivers only summary

This separation ensures the LLM's reasoning budget is spent on what matters (summaries) and not on procedural code.

## File Structure

```
transcription-pipeline/
├── README.md              ← This file
├── SKILL.md               ← Agent workflow (concise)
├── requirements.txt
├── .gitignore
└── scripts/
    └── run.py             ← Unified pipeline (3 strategies)
```

## Pitfalls

- **E3 before E2:** Always try chunked whisper (10 min) before whole-file. Chunking loads the model once, handles memory better.
- **yt-dlp output template (CRITICAL):** Use a temp directory with `%(id)s.%(ext)s` pattern. DO NOT use `NamedTemporaryFile` with `.mp3` suffix — yt-dlp treats it as a template and produces 0-byte files.
- **PATH for yt-dlp:** yt-dlp lives in `venv/bin/`. Bare `subprocess.run(['yt-dlp', ...])` calls fail with `FileNotFoundError` if venv bin isn't on PATH. Either prepend PATH or use the full path.
- **yt-dlp JS runtime:** Without `--extractor-args youtube:js_es=deno`, yt-dlp can fail silently on extraction. Always include this flag.
- **Whisper Segment API:** `faster_whisper` segments do NOT have `.duration`. Use `segment.end - segment.start`.
- **Audio cleanup:** Always use `shutil.rmtree()` on the parent temp directory, not `os.remove()` on the file. The file sits in a dedicated temp dir.
- **Language detection:** Capture `info.language` from `segs, info = model.transcribe(...)`. Language is NOT in the segment objects.
- **stdout buffering:** In scripts run as background processes, stdout needs `sys.stdout.reconfigure(line_buffering=True)` to flush output in real time.

## Vault Interlinking (Append-Only)

After saving a new vault note, scan existing transcripts and add `[[wikilinks]]` between related ones (shared topics: agents, models, tools, themes).

**CRITICAL: Append-only pattern.** Never read the whole file and rewrite it. `read_file()` from agent tools adds line numbers that corrupt the file.

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

## Error Handling

- **Transcript disabled / API failure:** `scripts/run.py` handles cascade automatically: E1 fails → E3 (chunked whisper) → E2 (whole file). No user intervention needed.
- **Private/unavailable video:** relay the error and ask the user to verify the URL.
- **Dependency missing:** install requirements from `requirements.txt`.
- **All strategies fail:** Script outputs `{"error": "All strategies failed", "video_id": "..."}`. Double-check URL and dependencies.

