# Scripts API Reference

Full Python API for each pipeline script. All scripts live in `SKILL_DIR/scripts/`.

---

## inspect_media.py

### CLI
```bash
python inspect_media.py <media_file>
```
Returns JSON to stdout. Exits 1 on error.

### Python API
```python
def inspect_media(filepath: str) -> dict:
    """Inspect a media file and return structured technical metadata.

    Returns:
        duration_sec: Total duration in seconds (float | None).
        size_bytes: File size in bytes (int).
        bitrate_kbps: Overall bitrate in kbps (float | None).
        has_audio_track: Whether an audio track was found (bool).
        audio_channels: Number of channels (int | None).
        audio_sample_rate: Sample rate in Hz (int | None).
        video_codec: Video codec name (str | None).
        audio_codec: Audio codec name (str | None).
        container: Detected container format (str).
        risks: List of risk strings detected (list[str]).
    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file is empty (0 bytes).
    """
```

### Fallback chain
1. `ffprobe` (subprocess) — full metadata including codec, bitrate, streams
2. Pure-Python WAV header parser (struct) — sample rate, channels, bit depth, duration
3. File-size estimation for CBR audio (MP3/OGG/AAC/WMA) — rough duration

---

## prepare_audio.py

### CLI
```bash
python prepare_audio.py <source> --workspace <dir> [--sample-rate 16000] [--codec pcm_s16le]
```
Prints the output WAV path to stdout.

### Python API
```python
def prepare_audio(
    source_path: str,
    workspace: str = ".",
    sample_rate: int = 16000,
    codec: str = "pcm_s16le",
) -> str:
    """Extract and normalize audio to 16-bit PCM mono WAV.

    Returns absolute path to prepared WAV.

    Raises:
        FileNotFoundError: If source doesn't exist.
        ValueError: If source is empty.
        RuntimeError: If all extraction methods fail.
    """
```

### Idempotency
If `audio/prepared_audio.wav` exists with mtime ≥ source mtime, skips re-extraction. Fast-path: if source is already 16kHz mono 16-bit WAV, copies instead of transcoding.

### Fallback chain
1. ffmpeg (primary)
2. Python `wave` module (WAV sources only — supports sample rate conversion via linear interpolation + stereo downmix)

---

## chunk_audio.py

### CLI
```bash
python chunk_audio.py <audio_path> [--workspace <dir>] [--target-duration 600] \
  [--overlap 5] [--policy auto] [--total-duration <float>] [--media-info <json>]
```
Prints decision summary + JSON to stdout.

### Python API

```python
def decide_chunking(
    media_info: dict,
    policy: str = "auto",  # "auto" | "always" | "never"
    target_duration_sec: int = 600,
    engine_limits: dict | None = None,
) -> dict:
    """Decide whether chunking is needed.

    Returns:
        enabled: bool
        reason: str
        target_duration_sec: int
        overlap_sec: int

    Guardrails (applied even in auto mode):
        - Duration < 60s → never chunk
        - Duration < target_duration_sec * 1.5 → never chunk
        - engine_limits may also prevent chunking
    """

def create_audio_chunks(
    audio_path: str,
    target_duration_sec: int = 600,
    overlap_sec: int = 5,
    workspace: str = ".",
    total_duration: float | None = None,
    chunking_enabled: bool = True,
) -> list[dict]:
    """Split audio into deterministic overlapping chunks.

    Each chunk dict:
        chunk_id: str          (e.g., "chunk_0001")
        chunk_path: str | None (None if creation failed)
        start_sec: float
        end_sec: float
        duration_sec: float
        overlap_before_sec: float
        overlap_after_sec: float
        status: "ready" | "failed" | "pending"
        attempts: int
        transcript_path: str | None
    """

def validate_coverage(
    chunks: list[dict],
    total_duration: float,
) -> list[str]:
    """Check chunk coverage. Returns warning strings (empty = full coverage)."""
```

### Naming
`chunk_NNNN_HH-MM-SS_HH-MM-SS.wav` — deterministic, source-relative timestamps.

### Chunking policy logic
| policy=auto | Duration < 60s | Duration 60s–1.5x target | Duration > 1.5x target |
|---|---|---|---|
| Action | No chunking | No chunking | Chunk |

---

## transcribe_units.py

### CLI
```bash
python transcribe_units.py <unit_path> [--config <json>] [--simulate]
python transcribe_units.py <transcript_json> --validate
```

### Python API

```python
def transcribe_unit(
    unit_path: str,
    unit_info: dict,   # chunk_id, start_sec, end_sec, duration_sec
    config: dict,      # engine, language, whisper_bin, whisper_model, simulate
) -> dict:
    """Transcribe a single audio unit.

    Returns:
        chunk_id: str
        text: str
        start_sec: float  (source-relative)
        end_sec: float
        language: str
        confidence: float  (0.0-1.0)
        segments: list[dict]  (each: start, end, text, confidence, words)
        word_count: int
        engine: str
        status: "completed" | "failed"

    NOTE: Simulated mode bypasses file-existence checks.
    Non-simulated engines WILL raise FileNotFoundError if the file
    does not exist on disk.
    """

def transcribe_with_retries(
    unit_path: str,
    unit_info: dict,
    config: dict,
) -> dict:
    """Transcribe with 3-attempt escalating retry.

    Attempts:
        1. Direct transcription of original chunk
        2. Halve chunk duration, retry
        3. Apply audio cleanup (volume boost), retry

    On final failure: returns status="failed_final" with
    text="[MISSING TRANSCRIPTION: chunk_NNNN from HH:MM:SS to HH:MM:SS]"
    """

def validate_transcription(transcript: dict) -> bool:
    """Structural validation of a result dict.
    Checks required fields, non-empty text, reasonable timestamps,
    consistent segment structure.
    """
```

### Engine config
```json
{"engine": "simulated", "language": "en"}
{"engine": "whisper-cpp", "whisper_bin": "whisper-cpp", "whisper_model": "ggml-base.en.bin"}
```

### Simulated mode
Generates fake segments (~1 per 3 seconds) from a lorem-ipsum-style word pool. Confidence varies from 0.85–0.94. Use for testing the pipeline without real audio.

---

## recompose_transcript.py

### CLI
```bash
python recompose_transcript.py <transcripts_json> [--overlap 5] [--chunking-enabled] [-o <output>]
```
Input: JSON file with a list of per-chunk transcript dicts (or a dict with a `"transcripts"` key).

### Python API

```python
def normalize_timestamps(
    transcripts: list[dict],
    chunking_enabled: bool,
    overlap_sec: int = 5,
) -> list[dict]:
    """Convert chunk-relative timestamps to source-absolute.
    Each segment's start/end gets chunk_start added. Sorts by start_sec.
    Adjusts word-level timestamps too.
    """

def reconcile_overlaps(
    segments: list[dict],
    overlap_sec: int = 5,
) -> list[dict]:
    """Deduplicate overlapping regions between chunks.

    For overlapping segments from different chunks:
    1. Compute Jaccard similarity on word sets
    2. If similarity > 0.5 → likely duplicate → keep higher-confidence source
    3. If similarity ≤ 0.5 → likely different content (speaker change) → keep both
    """

def assemble_transcript(segments: list[dict]) -> str:
    """Concatenate into formatted text with [HH:MM:SS] headers every 30s."""

def recompose(
    transcripts: list[dict],
    chunking_enabled: bool = False,
    overlap_sec: int = 5,
) -> dict:
    """Full reconstruction: normalize → reconcile → assemble.

    Returns:
        segments: list[dict]  (sorted, deduplicated, with segment_index)
        full_text: str
        total_duration_sec: float
        warnings: list[str]
        segment_count: int
        word_count: int
    """

def load_transcripts(transcripts_data) -> list[dict]:
    """Flexible input parser. Accepts list, JSON file path, or JSON string.
    Handles dicts with 'transcripts', 'chunks', 'results', 'segments' keys.
    """
```

### Input flexibility
Can accept:
- A list of transcript dicts directly
- A JSON file path containing the list
- A JSON string
- A dict with `"transcripts"` key (or `"chunks"`, `"results"`, `"segments"`)
- A single transcript dict (wraps in list)

---

## render_outputs.py

### CLI
```bash
python render_outputs.py <final_data_json> --workspace <dir> [--formats markdown,json,srt,vtt,txt]
python render_outputs.py --list-formats
```

### Python API

```python
def render_markdown(
    transcript_data: dict,
    template_path: str | None = None,
    output_path: str | None = None,
) -> str:
    """YAML header (title, date, duration, word_count) + [HH:MM:SS] sections.
    Renders failure markers as blockquotes, silence as italic, low-confidence as italic+label.
    """

def render_json(
    segments: list[dict],
    output_path: str | None = None,
) -> str:
    """Clean JSON with index, start_sec, end_sec, text, confidence, source_chunk, words[]."""

def render_srt(segments: list[dict], output_path: str | None = None) -> str:
    """SubRip format. Skips segments < 0.5s. Indexed 1..N."""

def render_vtt(segments: list[dict], output_path: str | None = None) -> str:
    """WebVTT format with Kind: captions header and date."""

def render_plain_text(segments: list[dict], output_path: str | None = None) -> str:
    """Space-joined plain text without timestamps."""

def build_manifest(
    final_data: dict,
    workspace: str,
    outputs: dict[str, str],
    parameters: dict | None = None,
) -> dict:
    """Full manifest with quality scoring.

    Quality score (0.0-1.0) = 0.5*coverage + 0.3*confidence + 0.2*(1-failure_ratio)
    Warning if score < 0.5.
    """

def render_outputs(
    final_data: dict,
    workspace: str = ".",
    output_formats: list[str] | None = None,
    parameters: dict | None = None,
) -> dict[str, str]:
    """Orchestrate all renders. Returns {format: output_path} dict.
    Always includes 'manifest' key.
    """
```

### Output format mapping
| format | filename | content |
|---|---|---|
| markdown | `transcript_final.md` | YAML header + temporal sections |
| json | `transcript_segments.json` | Structured segment array |
| srt | `subtitles.srt` | SubRip subtitles |
| vtt | `subtitles.vtt` | WebVTT subtitles |
| txt | `transcript_final.txt` | Plain text, no timestamps |
| manifest | `manifest.json` | Full pipeline manifest (auto-generated) |

---

## Pipeline orchestration pattern

Typical Python usage end-to-end:

```python
info = inspect_media.inspect_media("source.mp4")
decision = chunk_audio.decide_chunking(info, policy="auto")
audio_path = prepare_audio.prepare_audio("source.mp4", workspace=ws)
chunks = chunk_audio.create_audio_chunks(audio_path, total_duration=info["duration_sec"], chunking_enabled=decision["enabled"], workspace=ws)
transcripts = []
for c in chunks:
    t = transcribe_units.transcribe_with_retries(c["chunk_path"], c, {"engine": "simulated"})
    transcripts.append(t)
final = recompose_transcript.recompose(transcripts, chunking_enabled=decision["enabled"])
outputs = render_outputs.render_outputs(final, workspace=ws, output_formats=["markdown", "json"])
```

---

## Error handling pattern

All scripts follow the same rule:
- **Simulated mode** (transcribe_units): file-existence checks happen AFTER engine dispatch — never before. This lets tests and development workflows work without real audio files.
- **Real engines**: FileNotFoundError raised immediately if the chunk file doesn't exist on disk.
- **Failures** in the pipeline always produce structured markers (`[MISSING TRANSCRIPTION: ...]`) — never silent drops.
