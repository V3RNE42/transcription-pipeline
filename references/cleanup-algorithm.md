# Cleanup Algorithm — YouTube Transcript to ~30s Blocks

Used by the `youtube-content` skill's full pipeline (Phase 3) and the `transcription-pipeline` skill's Phase 9.

## Pre-Step: Deduplicate Adjacent Text (Auto-Generated Captions Only)

Auto-generated captions (especially from yt-dlp SRT extraction) emit each spoken phrase as 3 consecutive segments: the phrase text, a ~10ms blank, then the same phrase again. This quadruples segment count (~2.5K→10K lines) and triplicates every line in the final transcript.

Apply BEFORE the main block-grouping step:

```python
def deduplicate_adjacent(segments, min_gap=0.5):
    """Merge runs of near-identical segments from auto-generated captions.
    
    Keeps the segment with the longest text from each contiguous run
    (adjacent segments with gap < min_gap seconds).
    """
    deduped = []
    run = []
    for s in segments:
        if run and abs(s.start_sec - run[-1].end_sec) < min_gap:
            run.append(s)
        else:
            if run:
                deduped.append(max(run, key=lambda x: len(x.text)))
            run = [s]
    if run:
        deduped.append(max(run, key=lambda x: len(x.text)))
    return deduped
```

Typical compression: 2174 segments → ~900 deduplicated segments (~2.4×).

## Main Algorithm

```python
import re, textwrap

def cleanup_segments(segments, block_size=30):
    """Group raw transcript segments into ~30s readable blocks.

    Args:
        segments: List of objects with .start (float), .text (str)
        block_size: Target seconds per block (default: 30)

    Returns:
        List of (start_seconds, cleaned_text) tuples
    """
    blocks = []
    current = []
    block_start = 0
    for s in segments:
        if current and s.start >= block_start + block_size:
            text = " ".join(c.text.strip() for c in current)
            text = re.sub(r'\s+', ' ', text)          # collapse whitespace
            text = re.sub(r'\s([?.!,"])', r'\1', text) # fix punctuation spacing
            blocks.append((block_start, text))
            block_start = int(s.start // block_size) * block_size
            current = [s]
        else:
            if not current:
                block_start = int(s.start // block_size) * block_size
            current.append(s)
    if current:
        text = " ".join(c.text.strip() for c in current)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\s([?.!,"])', r'\1', text)
        blocks.append((block_start, text))
    return blocks
```

## Rendering to Markdown

```python
def fmt_ts(secs):
    m, s = divmod(int(secs), 60)
    return f"{m:02d}:{s:02d}"

def render_blocks(blocks):
    lines = []
    for start_sec, text in blocks:
        lines.append(f"### [{fmt_ts(start_sec)}]")
        lines.append(textwrap.fill(text, width=80))
        lines.append("")
    return "\n".join(lines)
```

## Behavior

- Raw YouTube API returns ~2-5s segments per subtitle line
- This groups them into ~30s windows for readability
- Block start time snaps to the nearest 30s boundary of the first segment in the block
- The `### [MM:SS]` marker is always present for navigation
- Text is wrapped at 80 chars so it renders cleanly in terminals and Markdown viewers
