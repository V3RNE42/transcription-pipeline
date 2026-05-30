# yt-dlp SRT Pipeline for YouTube Transcripts

**⚠️ PREFER TTML OVER SRT FOR AUTO-CAPTIONS.** The TTML format (`--sub-format ttml`) avoids the triplicate text issue entirely — no `\r\n` normalization, no duration filtering, no running-text dedup needed. See `references/yt-dlp-ttml-pipeline.md`. Use SRT only when TTML is unavailable (manual subtitles) or when you specifically need SRT output.

## When to Use

As the fetch step in the YouTube Integration code-first pipeline (Phase 1 of the sequential pipeline), **only when TTML is unavailable** (manual subtitles, or auto-captions exist but you specifically need SRT output). TTML (`--sub-format ttml`) is the preferred format for auto-captions — see `references/yt-dlp-ttml-pipeline.md`.

## Exact Commands

### Fetch auto-generated captions

```bash
yt-dlp --skip-download --write-auto-subs --sub-lang {lang} --convert-subs srt --output "/tmp/%(id)s" "{yt_url}"
```

- `{lang}` = two-letter language code (`en`, `es`, `fr`, `de`, `ja`, etc.)
- `{yt_url}` = full YouTube URL
- Output: `/tmp/{yt_id}.{lang}.srt` (e.g. `/tmp/ByUz0W9UUEI.es.srt`)

### Get video metadata

```bash
yt-dlp --skip-download --print title --print channel --print channel_url --print upload_date --print duration_string "{yt_url}"
```

Outputs one line per field: title, channel, channel URL, upload date (YYYYMMDD), duration (MM:SS).

## SRT Structure Quirks (Auto-Generated Captions)

### 1. Triplicate text pattern

Each spoken phrase appears as **3 consecutive SRT blocks**:

```
42|00:00:25,080 --> 00:00:26,990
|the text here

43|00:00:26,990 --> 00:00:27,000
|(blank ~10ms)

44|00:00:27,000 --> 00:00:29,029
|the text here
```

The pattern: phrase → blank (~10ms) → same phrase. This triplicates the output.

### 2. Window-style line endings

All SRT files from `yt-dlp --convert-subs srt` use `\r\n` (CRLF) even on Linux. Normalize before parsing.

### 3. Segment fragmentation

A single sentence may be split across multiple SRT blocks with only a few ms gap between them. This is normal.

## Deduplication Recipe

⚠️ **The gap-based `deduplicate_adjacent` is deprecated.** In auto-generated YouTube captions, the gap between EVERY consecutive segment is 0–10ms. This causes gap-based grouping to sweep ALL segments into one giant run, keeping only the single longest text — losing everything else. Use the two-phase approach below.

### Overview

Auto-generated YouTube captions (fetched via yt-dlp SRT) have two distinct duplication patterns:

1. **Triplicate fragments:** Every spoken phrase is preceded/followed by a 10ms "echo" segment with partial text. Remove by filtering segments with duration < 0.2s.
2. **Overlapping text:** After filtering, adjacent segments overlap: the tail of seg[N] is the head of seg[N+1]. Remove with a running-text word-overlap accumulator.

### Phase 1: Parse SRT (line-by-line)

Use line-by-line parsing — block splitting can misalign with inconsistent blank-line spacing in auto-generated SRT:

```python
import re

def parse_srt_linewise(srt_text):
    """Parse SRT into [{start_sec, end_sec, text, dur}] via line iteration."""
    srt_text = srt_text.replace('\r\n', '\n').replace('\r', '\n')
    
    def ts_to_sec(ts):
        ts = ts.replace(',', '.')
        parts = ts.split(':')
        return int(parts[0])*3600 + int(parts[1])*60 + float(parts[2])
    
    segments = []
    lines = srt_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or not line.isdigit():
            i += 1; continue
        if i + 1 >= len(lines): break
        ts_line = lines[i+1].strip()
        ts_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})', ts_line
        )
        if not ts_match:
            i += 1; continue
        text_parts = []
        j = i + 2
        while j < len(lines) and lines[j].strip():
            text_parts.append(lines[j].strip())
            j += 1
        text = ' '.join(text_parts).strip()
        if text:
            start = ts_to_sec(ts_match.group(1))
            end = ts_to_sec(ts_match.group(2))
            segments.append({"start_sec": start, "end_sec": end, "text": text, "dur": end - start})
        i = j
    return segments
```

### Phase 2: Filter short fragments

Remove 10ms "echo" segments by duration:

```python
segs = [s for s in segments if s["dur"] >= 0.2]
```

### Phase 3: Running-text word-overlap dedup

```python
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

### Verified Metrics (13-min video, 780s)

| Stage | Count | Compression |
|-------|-------|-------------|
| Raw SRT segments | 768 | — |
| After duration filter (≥0.2s) | 384 | 2× |
| After running-text dedup | 384 segs, ~14.5K chars | 5.3× semantic |
| Clean text | No duplicate words | Clean |

## Verification Metrics

### Old benchmark (4-video batch from prior session — gap-based dedup)

| Video | Duration | Raw Segments | After Old Dedup | Coverage | Gaps>2s |
|-------|----------|-------------|-----------------|----------|---------|
| Rallo (vivienda) | 39:47 | 2174 | ~900 | 99.8% | 1 |
| MOSAIC-GS | 5:29 | 286 | ~120 | 95.4% | 0 |
| Manipulación | 36:38 | 1890 | ~790 | 99.2% | 3 |
| Text Embeddings | 19:56 | 964 | ~400 | 99.7% | 0 |

⚠️ The gap-based dedup used above worked on these videos because they had occasional gaps >0.5s between triplicate groups. On videos with continuous ≤10ms gaps between ALL segments (common in many auto-generated SRTs), this approach collapses everything into a single run. Use the two-phase approach instead (see Deduplication Recipe above).

### New benchmark (running-text dedup, verified today)

| Video | Duration | Raw SRTSegs | After Filter | After Dedup | Clean Chars |
|-------|----------|-------------|--------------|-------------|-------------|
| ¿Es Hora de Dejar tu Monolito? | 13:00 | 768 | 384 | 384 (no word dups) | ~14,500 |

## Pitfalls

1. **Do NOT use `read_file` to load SRT** — it defaults to 500 lines. SRT files for 30+ min videos are 3K-11K lines. Use Python's `open(fn).read()`.
2. **Always normalize `\r\n` before regex parsing** — the pattern `\n\s*\n` fails to split blocks on CRLF.
3. **Do not skip deduplication** — without it, the cleaned transcript is 3x longer with identical repeated phrases.
4. **Subtitle overlap segments** — auto-generated captions often have 10ms segments with just a space. These are noise, not real gaps.
