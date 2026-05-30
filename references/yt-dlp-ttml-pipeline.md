# yt-dlp TTML Pipeline for YouTube Transcripts

## Why TTML over SRT

Auto-generated YouTube captions downloaded as SRT via `--convert-subs srt` produce **triplicate text**: each spoken phrase appears as 3 consecutive SRT blocks (phrase → ~10ms blank → same phrase). The SRT pipeline requires two-phase deduplication (duration filter + running-text accumulator).

TTML (Timed Text Markup Language) avoids this entirely. The XML segments from `--sub-format ttml` have clean boundaries with no triplication. Parse directly with `xml.etree.ElementTree`.

## Exact Command

```bash
yt-dlp --skip-download --write-auto-subs --sub-lang es-orig,en-orig,es,en --sub-format ttml --output "/tmp/%(id)s" "{yt_url}"
```

### Language Code Priority

Always try **original-language codes** first:
- `es-orig` — Spanish original (for Spanish audio)
- `en-orig` — English original (for English audio)
- `es` — Spanish auto-translated (fallback)
- `en` — English auto-translated (fallback)

Original codes give the source audio's direct ASR transcription. Translated codes give machine-translated versions from that ASR.

Use `--list-subs` first to check available languages:
```bash
yt-dlp --list-subs "{yt_url}" 2>&1 | grep -E "^es|^en|Available|has no"
```

## TTML Parsing Recipe

### Phase 1: Parse TTML with ElementTree

```python
import xml.etree.ElementTree as ET
import re

tree = ET.parse("/tmp/yt_video.en-orig.ttml")
root = tree.getroot()
ns = {'tt': 'http://www.w3.org/ns/ttml'}

segments = []
for p in root.findall('.//tt:p', ns):
    begin = p.get('begin', '0s')
    text = ''.join(p.itertext()).strip()
    if not text:
        continue
    # Parse time format "00:00:03.000" or "2.5s"
    m = re.match(r'(\d+):(\d+):(\d+)\.(\d+)', begin)
    if m:
        total_s = int(m[1])*3600 + int(m[2])*60 + int(m[3])
    else:
        m2 = re.match(r'([\d.]+)s', begin)
        total_s = float(m2[1]) if m2 else 0
    segments.append({"start_sec": total_s, "text": text})
```

### Phase 2: Sort (if needed)

TTML segments are typically in order, but sort to be safe:
```python
segments.sort(key=lambda s: s["start_sec"])
```

### Phase 3: Write to Markdown

```python
for seg in segments:
    m, s = int(seg["start_sec"]//60), int(seg["start_sec"]%60)
    f.write(f"[{m:02d}:{s:02d}] {seg['text']}\n")
```

No deduplication needed — TTML segments are clean singletons.

## Verified Metrics

| Video | Duration | Language | TTML Segments | 
|-------|----------|----------|---------------|
| Power Automate desde CERO (Founderz School) | 37:18 | es-orig | 972 |
| Better Than Agent Skills | 4:42 | en-orig | 158 |

TTML output is ready-to-use with no dedup, no `\r\n` normalization, no triplicate filtering.

## Comparison vs SRT Pipeline

| Aspect | TTML | SRT |
|--------|------|-----|
| Text triplication | None | 3× (phrase → blank → phrase) |
| Line endings | Native XML | `\r\n` (always CRLF) |
| Parsing complexity | Single pass with ElementTree | Line-by-line state machine |
| Dedup needed | No | Two-phase (duration filter + running-text accumulator) |
| Segment metadata | `begin` attribute as timestamp | Full `start --> end` range |
| Language codes | Supports `es-orig`, `en-orig` | Supports same codes |
| File size (37 min video) | ~15 KB | ~30-45 KB |

## Pitfalls

1. **Namespace required.** The TTML namespace `http://www.w3.org/ns/ttml` must be declared in `findall()`. Without it, no `<p>` elements are found.
2. **No `end` attribute in auto-generated TTML.** Auto-generated TTML only has `begin` timestamps, not `end`. Compute segment end from the next segment's start, or skip silences entirely (each segment covers one spoken utterance).
3. **TTML `begin` uses `HH:MM:SS.mmm` format** (not `HH:MM:SS,mmm` as SRT does). No comma-to-dot normalization needed.
4. **Always use `skip-download`.** The `--write-auto-subs` flag combined with `--skip-download` downloads captions without the video. Omit `--skip-download` and it downloads both, wasting bandwidth.
5. **Language list has hundreds of entries.** When checking `--list-subs`, grep for `es-orig`, `en-orig`, `es`, or `en` specifically. The full list spans 100+ languages and is noisy.
