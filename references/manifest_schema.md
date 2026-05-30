# Manifest Schema Reference

> **File**: `manifest.json` — the central state file for the transcription pipeline.
> Every stage reads from and writes to this manifest. It enables resumability,
> error tracking, and output artifact discovery.

---

## Top-Level Structure

```json
{
  "manifest_version": "1.0",
  "pipeline_id": "uuid-v4",
  "source": "/path/to/original/media.wav",
  "created_at": "2026-05-16T21:00:00Z",
  "updated_at": "2026-05-16T21:05:30Z",
  "status": "completed",

  "media_info": { ... },
  "chunking": { ... },
  "chunks": [ ... ],
  "transcription": { ... },
  "outputs": { ... },
  "quality": { ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `manifest_version` | string | Schema version for forward compatibility |
| `pipeline_id` | string | UUID v4 — unique pipeline run identifier |
| `source` | string | Absolute path to the original media file |
| `created_at` | string (ISO 8601) | Timestamp when pipeline started |
| `updated_at` | string (ISO 8601) | Timestamp of last manifest write |
| `status` | enum | One of: `initialized`, `preparing`, `chunking`, `transcribing`, `assembling`, `completed`, `failed` |

---

## `media_info` Section

Captures metadata about the source media file.

```json
"media_info": {
  "duration_sec": 3721.5,
  "file_size_bytes": 148920000,
  "sample_rate_hz": 16000,
  "bit_depth": 16,
  "channels": 1,
  "codec": "pcm_s16le",
  "format": "wav",
  "risks": ["large_file_>100MB", "long_duration_>30min"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `duration_sec` | float | Total duration in seconds |
| `file_size_bytes` | int | File size in bytes |
| `sample_rate_hz` | int | Audio sample rate (e.g. 16000, 44100) |
| `bit_depth` | int | Bits per sample (e.g. 16, 24, 32) |
| `channels` | int | Number of audio channels |
| `codec` | string | Audio codec name (e.g. `pcm_s16le`, `aac`, `mp3`) |
| `format` | string | Container format (e.g. `wav`, `mp3`, `m4a`, `ogg`) |
| `risks` | array[string] | List of risk flags identified during analysis. Possible values: `large_file_>100MB`, `long_duration_>30min`, `high_sample_rate_>48kHz`, `multi_channel`, `variable_bitrate`, `low_bitrate_<64kbps`, `truncated_duration_<1s` |

---

## `chunking` Section

Global chunking configuration and aggregate statistics.

```json
"chunking": {
  "enabled": true,
  "policy": "time_based",
  "target_chunk_duration_sec": 300.0,
  "chunk_overlap_sec": 15.0,
  "total_chunks": 13,
  "coverage_start_sec": 0.0,
  "coverage_end_sec": 3721.5,
  "gap_detected": false,
  "gaps": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Whether chunking was applied |
| `policy` | string | Chunking strategy used. Values: `time_based` (fixed duration), `silence_based` (split on silence), `segment_based` (VAD segments) |
| `target_chunk_duration_sec` | float | Target duration per chunk in seconds |
| `chunk_overlap_sec` | float | Overlap between adjacent chunks for seam reconciliation |
| `total_chunks` | int | Number of chunks produced |
| `coverage_start_sec` | float | Absolute start of coverage (usually 0.0) |
| `coverage_end_sec` | float | Absolute end of coverage (matches `duration_sec`) |
| `gap_detected` | bool | True if any coverage gap was found between chunks |
| `gaps` | array[object] | List of gap intervals, each `{ "start_sec": float, "end_sec": float, "duration_sec": float }` |

---

## `chunks` Array

Per-chunk state — the heart of resumability.

```json
"chunks": [
  {
    "chunk_id": "chunk_000",
    "source_path": "/path/to/original/media.wav",
    "chunk_path": "/path/to/output/chunks/chunk_000.wav",
    "start_sec": 0.0,
    "end_sec": 300.0,
    "duration_sec": 300.0,
    "overlap_before_sec": 0.0,
    "overlap_after_sec": 15.0,
    "status": "completed",
    "attempts": 1,
    "last_error": null,
    "transcript_path": "/path/to/output/transcripts/chunk_000.json"
  },
  {
    "chunk_id": "chunk_001",
    "source_path": "/path/to/original/media.wav",
    "chunk_path": "/path/to/output/chunks/chunk_001.wav",
    "start_sec": 285.0,
    "end_sec": 585.0,
    "duration_sec": 300.0,
    "overlap_before_sec": 15.0,
    "overlap_after_sec": 15.0,
    "status": "failed_retryable",
    "attempts": 2,
    "last_error": "whisper_api_timeout",
    "transcript_path": null
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | string | Zero-padded identifier (e.g. `chunk_000`, `chunk_001`) |
| `source_path` | string | Path to the original source media |
| `chunk_path` | string | Path to the extracted audio chunk file |
| `start_sec` | float | Absolute start time in the source (seconds) |
| `end_sec` | float | Absolute end time in the source (seconds) |
| `duration_sec` | float | `end_sec - start_sec` |
| `overlap_before_sec` | float | Overlap with previous chunk (0 for first chunk) |
| `overlap_after_sec` | float | Overlap with next chunk (0 for last chunk) |
| `status` | enum | One of: `pending`, `processing`, `completed`, `failed_retryable`, `failed_final` |
| `attempts` | int | Number of transcription attempts so far |
| `last_error` | string\|null | Error message from the last failed attempt |
| `transcript_path` | string\|null | Path to per-chunk transcript file, or null if not yet transcribed |

### Chunk Status State Machine

```
pending → processing → completed
                  ↓
          failed_retryable → processing (retry)
                  ↓ (max retries exceeded)
          failed_final
```

---

## `transcription` Section

Global transcription engine configuration and aggregate results.

```json
"transcription": {
  "engine": "whisper",
  "engine_version": "large-v3-turbo",
  "language": "es",
  "language_detected": "es",
  "confidence": 0.97,
  "total_chunks_transcribed": 12,
  "failed_chunks": 1,
  "total_retries": 2,
  "started_at": "2026-05-16T21:02:00Z",
  "completed_at": "2026-05-16T21:05:30Z",
  "duration_elapsed_sec": 210.0,
  "real_time_factor": 17.7,
  "options": {
    "temperature": 0.0,
    "beam_size": 5,
    "word_timestamps": true,
    "vad_filter": true
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `engine` | string | Transcription engine (e.g. `whisper`, `deepgram`, `assemblyai`) |
| `engine_version` | string | Model version or tag |
| `language` | string\|null | Target language code (`null` = auto-detect) |
| `language_detected` | string\|null | Detected language (if auto-detect was used) |
| `confidence` | float\|null | Language detection confidence (0-1) |
| `total_chunks_transcribed` | int | Number of chunks with status `completed` |
| `failed_chunks` | int | Number of chunks with status `failed_final` |
| `total_retries` | int | Total retry attempts across all chunks |
| `started_at` | string (ISO 8601) | Transcription start timestamp |
| `completed_at` | string (ISO 8601) | Transcription completion timestamp |
| `duration_elapsed_sec` | float | Wall-clock time spent transcribing |
| `real_time_factor` | float | `duration_elapsed_sec / source_duration_sec` — lower is faster |
| `options` | object | Engine-specific transcription options |

---

## `outputs` Section

Paths to all generated output artifacts.

```json
"outputs": {
  "prepared_audio": "/path/to/output/prepared.wav",
  "chunks_dir": "/path/to/output/chunks/",
  "transcripts_dir": "/path/to/output/transcripts/",
  "assembled_transcript_raw": "/path/to/output/transcript_raw.json",
  "assembled_transcript_cleaned": "/path/to/output/transcript_cleaned.json",
  "assembled_transcript_txt": "/path/to/output/transcript.txt",
  "assembled_transcript_srt": "/path/to/output/transcript.srt",
  "assembled_transcript_vtt": "/path/to/output/transcript.vtt",
  "chunking_report": "/path/to/output/chunking_report.json",
  "quality_report": "/path/to/output/quality_report.json",
  "manifest": "/path/to/output/manifest.json"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `prepared_audio` | string\|null | Path to normalized/prepared audio (if preprocessing step applied) |
| `chunks_dir` | string | Directory containing audio chunk files |
| `transcripts_dir` | string | Directory containing per-chunk transcript files |
| `assembled_transcript_raw` | string | Full assembled transcript with overlaps (JSON, word-level timestamps) |
| `assembled_transcript_cleaned` | string | Final cleaned transcript with overlaps reconciled |
| `assembled_transcript_txt` | string | Plain text transcript |
| `assembled_transcript_srt` | string | SubRip subtitle file |
| `assembled_transcript_vtt` | string | WebVTT subtitle file |
| `chunking_report` | string | Detailed chunking report |
| `quality_report` | string | Quality check results report |
| `manifest` | string | Path to this manifest file itself |

---

## `quality` Section

Quality gate results — determines whether the pipeline output is usable.

```json
"quality": {
  "overall_status": "passed",
  "checks_performed": [
    "temporal_coverage",
    "segment_order",
    "final_artifacts_exist",
    "overlap_duplicates",
    "per_chunk_failures",
    "readability"
  ],
  "checks": {
    "temporal_coverage": {
      "status": "passed",
      "detail": "100% coverage (0.0s to 3721.5s)"
    },
    "segment_order": {
      "status": "passed",
      "detail": "All 13 segments in chronological order"
    },
    "final_artifacts_exist": {
      "status": "passed",
      "detail": "All 7 expected output files found"
    },
    "overlap_duplicates": {
      "status": "warning",
      "detail": "2 duplicate segments found in overlap regions, auto-reconciled"
    },
    "per_chunk_failures": {
      "status": "partial_pass",
      "detail": "1 chunk failed_final (chunk_001 - 285.0s-585.0s), remaining 12/13 complete"
    },
    "readability": {
      "status": "passed",
      "detail": "Average word confidence: 0.94, no low-confidence segments"
    }
  },
  "warnings": [
    "2 duplicate segments auto-reconciled in overlap regions"
  ],
  "blocking_errors": [],
  "partial_errors": [
    "1 chunk failed permanently (chunk_001), gap from 285.0s to 585.0s"
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `overall_status` | enum | One of: `passed`, `partial_pass`, `failed` |
| `checks_performed` | array[string] | List of quality check names that were executed |
| `checks` | object | Map of check name → check result |
| `warnings` | array[string] | Non-blocking issues found |
| `blocking_errors` | array[string] | Issues that block pipeline from producing output |
| `partial_errors` | array[string] | Issues that produce partial but acceptable output |

### Per-Check Result Object

```json
{
  "status": "passed",
  "detail": "Human-readable explanation of the check result"
}
```

Status values: `passed`, `warning`, `partial_pass`, `failed`, `skipped`.

---

## Complete Example Manifest (Minimal)

```json
{
  "manifest_version": "1.0",
  "pipeline_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "source": "/data/recordings/meeting_2026-05-16.wav",
  "created_at": "2026-05-16T21:00:00Z",
  "updated_at": "2026-05-16T21:05:30Z",
  "status": "completed",
  "media_info": {
    "duration_sec": 1860.0,
    "file_size_bytes": 59520000,
    "sample_rate_hz": 16000,
    "bit_depth": 16,
    "channels": 1,
    "codec": "pcm_s16le",
    "format": "wav",
    "risks": ["long_duration_>30min"]
  },
  "chunking": {
    "enabled": true,
    "policy": "time_based",
    "target_chunk_duration_sec": 300.0,
    "chunk_overlap_sec": 15.0,
    "total_chunks": 7,
    "coverage_start_sec": 0.0,
    "coverage_end_sec": 1860.0,
    "gap_detected": false,
    "gaps": []
  },
  "chunks": [
    {
      "chunk_id": "chunk_000",
      "source_path": "/data/recordings/meeting_2026-05-16.wav",
      "chunk_path": "/data/output/chunks/chunk_000.wav",
      "start_sec": 0.0,
      "end_sec": 300.0,
      "duration_sec": 300.0,
      "overlap_before_sec": 0.0,
      "overlap_after_sec": 15.0,
      "status": "completed",
      "attempts": 1,
      "last_error": null,
      "transcript_path": "/data/output/transcripts/chunk_000.json"
    }
  ],
  "transcription": {
    "engine": "whisper",
    "engine_version": "large-v3-turbo",
    "language": "es",
    "total_chunks_transcribed": 7,
    "failed_chunks": 0,
    "total_retries": 0,
    "started_at": "2026-05-16T21:02:00Z",
    "completed_at": "2026-05-16T21:04:30Z",
    "duration_elapsed_sec": 150.0,
    "real_time_factor": 12.4,
    "options": { "temperature": 0.0, "word_timestamps": true }
  },
  "outputs": {
    "prepared_audio": "/data/output/prepared.wav",
    "chunks_dir": "/data/output/chunks/",
    "transcripts_dir": "/data/output/transcripts/",
    "assembled_transcript_raw": "/data/output/transcript_raw.json",
    "assembled_transcript_cleaned": "/data/output/transcript_cleaned.json",
    "assembled_transcript_txt": "/data/output/transcript.txt",
    "assembled_transcript_srt": "/data/output/transcript.srt",
    "assembled_transcript_vtt": "/data/output/transcript.vtt",
    "chunking_report": "/data/output/chunking_report.json",
    "quality_report": "/data/output/quality_report.json",
    "manifest": "/data/output/manifest.json"
  },
  "quality": {
    "overall_status": "passed",
    "checks_performed": [
      "temporal_coverage",
      "segment_order",
      "final_artifacts_exist",
      "overlap_duplicates",
      "per_chunk_failures",
      "readability"
    ],
    "checks": {
      "temporal_coverage": { "status": "passed", "detail": "100% coverage" },
      "segment_order": { "status": "passed", "detail": "All 7 segments in order" },
      "final_artifacts_exist": { "status": "passed", "detail": "All artifacts present" },
      "overlap_duplicates": { "status": "passed", "detail": "No duplicates found" },
      "per_chunk_failures": { "status": "passed", "detail": "0 failed chunks" },
      "readability": { "status": "passed", "detail": "Avg confidence 0.95" }
    },
    "warnings": [],
    "blocking_errors": [],
    "partial_errors": []
  }
}
```
