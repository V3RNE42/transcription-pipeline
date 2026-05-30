# Chunking Policy Reference

> Rules C1–C10 governing how the transcription pipeline splits long audio
> into manageable chunks, tracks them, and reassembles the final transcript.

---

## Chunking Rules (C1–C10)

### C1 — Chunking Decided Before Transcription

> The decision of whether to chunk MUST be made before any transcription begins,
> based on media analysis in the `media_info` section of the manifest.

**Rationale**: Avoids starting transcription of a large file, hitting a timeout
or memory limit, and having to restart. The decision is deterministic and
recorded upfront.

**Implementation**:
1. Extract `duration_sec` and `file_size_bytes` from `media_info`
2. Evaluate chunking conditions (see Activation Conditions below)
3. Set `chunking.enabled = true/false` in manifest
4. Proceed accordingly

---

### C2 — Operates on Prepared Audio Preferentially

> Chunking SHOULD operate on the prepared/normalized audio file rather than
> the original source, to ensure uniform sample rate and format across chunks.

**Rationale**: The prepare step normalizes sample rate, channel count, and
format. Chunking on this normalized file guarantees every chunk has identical
properties, avoiding per-chunk re-encoding.

**Implementation**:
- Use `outputs.prepared_audio` path for chunk extraction
- If no prepared audio exists, fall back to `source` path
- Record `source_path` per chunk for traceability

---

### C3 — Each Chunk Retains Absolute Start/End Times

> Every chunk MUST store its `start_sec` and `end_sec` as absolute offsets
> from the beginning of the original source file, not relative positions.

**Rationale**: Absolute timestamps are essential for:
- Reassembling transcripts in correct order
- Generating SRT/VTT subtitles with correct timestamps
- Reporting coverage gaps
- Allowing external tools to map segments to the original file

**Implementation**:
```python
chunk["start_sec"] = chunk_index * (target_duration - overlap)
chunk["end_sec"]   = min(start_sec + target_duration, total_duration)
```

---

### C4 — Chunks Cover Full Transcribable Duration

> The union of all chunk intervals MUST cover the entire transcribable duration
> of the source file, from `0.0` to `media_info.duration_sec`.

**Rationale**: Guarantees no audio is left untranscribed. Gaps are explicitly
detected and reported rather than silently skipping audio.

**Implementation**:
- After chunking, verify: `min(start_sec) == 0.0` and `max(end_sec) >= duration_sec`
- Track coverage in `chunking.coverage_start_sec` and `chunking.coverage_end_sec`
- Report any gaps in `chunking.gaps`

---

### C5 — Overlap Recorded and Reconciled

> Chunk overlap regions MUST be recorded in each chunk's metadata and reconciled
> during assembly to avoid duplicate text in the final transcript.

**Rationale**: Overlaps prevent loss at chunk boundaries (content "cut off" by
the split) but produce duplicate text segments. Reconciliation removes
duplicates while preserving the best transcription of boundary content.

**Implementation**:
- First and last chunks have `overlap_before_sec = 0` / `overlap_after_sec = 0` respectively
- Middle chunks have both overlaps set to `chunk_overlap_sec`
- During assembly: deduplicate segments in overlap regions by comparing
  word-level timestamps and keeping the higher-confidence version

---

### C6 — Errors Managed Per Chunk

> Transcription errors MUST be tracked independently per chunk, including
> retry attempts and failure state, so individual chunks can be retried
> without re-transcribing the entire file.

**Rationale**: A single failed chunk should not require re-transcribing
dozens of successfully completed chunks. Enables efficient recovery.

**Implementation**:
- Each chunk tracks: `status`, `attempts`, `last_error`
- Status transitions: `pending → processing → completed`
  - On error: `processing → failed_retryable → processing` (max 3 retries)
  - After max retries: `failed_retryable → failed_final`
- Assembly step handles `failed_final` chunks by marking gaps

---

### C7 — Final Output Uses Absolute Times

> The assembled full transcript MUST use absolute timestamps (matching the
> original source file), not relative chunk timestamps.

**Rationale**: Produces usable SRT/VTT files that map correctly to the
original media when played in a video/audio player.

**Implementation**:
- During assembly, add `chunk.start_sec` to each segment's timing
- Per-chunk transcripts store relative timestamps (0-based within chunk)
- Assembly step converts: `absolute_time = relative_time + chunk.start_sec`

---

### C8 — Gaps Marked Explicitly

> Any coverage gaps (from `failed_final` chunks or chunking errors) MUST be
> recorded in `chunking.gaps` and the assembly step MUST insert gap markers
> in the output transcript.

**Rationale**: A silent gap in the transcript is indistinguishable from
silence in the original audio. Explicit markers alert downstream consumers.

**Implementation**:
```json
// In manifest.chunking.gaps:
{ "start_sec": 285.0, "end_sec": 585.0, "duration_sec": 300.0, "reason": "chunk failed after 3 retries" }

// In transcript:
[GAP 285.0s–585.0s: transcription unavailable due to processing error]
```

---

### C9 — Manifest Allows Resumption

> The manifest file MUST contain sufficient state information to resume the
> pipeline from the point of interruption, without re-processing completed chunks.

**Rationale**: Enables crash recovery. If the pipeline is interrupted mid-run,
inspecting the manifest reveals exactly which chunks are done and which need
work.

**Implementation**:
- Before each chunk transcription, set `status = "processing"` and write manifest
- After each chunk completes, set `status = "completed"` and write manifest
- On restart: scan chunks for first `pending` or `failed_retryable` status
- Resume from that point

---

### C10 — Final Transcript Declares Direct vs Segmented

> The final assembled transcript MUST include a metadata field indicating
> whether it was produced directly (no chunking) or via segmentation (chunked).

**Rationale**: Downstream consumers need to know whether timestamps may have
reconciliation artifacts (segmented) or are direct from the engine (direct).

**Implementation**:
```json
{
  "metadata": {
    "production_method": "segmented",
    "chunks_used": 7,
    "overlap_reconciled": true
  }
}
```

Values: `"direct"` (no chunking applied), `"segmented"` (chunking applied).

---

## Chunking Activation Conditions

Chunking is activated when any of these conditions is met. Conditions are
evaluated in priority order — first match wins.

| # | Condition | Threshold | Rationale |
|---|-----------|-----------|-----------|
| 1 | Duration exceeds max | `duration_sec > 1800` (>30 min) | Prevents API timeouts (most ASR APIs cap at 30 min) |
| 2 | File size exceeds max | `file_size_bytes > 100_000_000` (>100 MB) | Prevents memory exhaustion and upload failures |
| 3 | Risk flag present | Any item in `media_info.risks` | Explicit risk flags from media analysis |
| 4 | Engine policy requires | Per-engine config | e.g. Whisher local model may cap at 600 sec |
| 5 | User overrides | CLI flag `--chunk / --no-chunk` | Explicit user preference overrides auto-detection |

### Default Behavior

- If no condition is met: chunking remains **disabled** (faster, simpler)
- If any condition is met: chunking **enabled** with the policy and duration below

---

## Suggested Chunk Durations by Scenario

| Scenario | Target Duration | Overlap | Policy | Rationale |
|----------|----------------|---------|--------|-----------|
| Podcast / meeting | 300s (5 min) | 15s | time_based | Good balance of context vs manageability |
| Lecture / class | 600s (10 min) | 20s | time_based | Longer context preserves narrative flow |
| Dictation / short | 120s (2 min) | 10s | time_based | Lower latency for near-realtime use |
| Interview (Q&A) | 300s (5 min) | 30s | silence_based | Overlap tuned for turn-taking seams |
| Legal deposition | 600s (10 min) | 15s | time_based | High accuracy priority, more context helps |
| Conference talk | 900s (15 min) | 20s | time_based | Minimize chunk count, talk has natural flow |
| Raw phone call | 180s (3 min) | 10s | time_based | Short segments for noisy audio |
| Low-resource language | 180s (3 min) | 30s | time_based | More overlap for context-dependent models |

### Overlap Duration Heuristic

```
overlap_sec = min(30, max(10, target_duration_sec * 0.05))
```

This ensures:
- Minimum overlap of 10s (enough for phrase completion)
- Maximum overlap of 30s (avoids excessive duplicate work)
- Scales proportionally for very long chunks

---

## Chunk ID Convention

```
chunk_{index:03d}
```

| Chunk | ID | Notes |
|-------|----|-------|
| First | `chunk_000` | overlap_before = 0 |
| Second | `chunk_001` | Standard middle chunk |
| Last | `chunk_NNN` | overlap_after = 0, may be shorter |

---

## Assembly Algorithm Pseudocode

```
1. Sort chunks by chunk_id (which implies chronological order)
2. For each completed chunk:
   a. Load per-chunk transcript (relative timestamps)
   b. Add chunk.start_sec to all timestamps → absolute timestamps
   c. If not first chunk and overlap_after > 0 from previous:
      - Find segments in overlap region (abs time overlap window)
      - Compare overlapping segments by confidence
      - Keep highest-confidence version, drop duplicates
   d. Append segments to assembled list
3. For each failed_final chunk:
   a. Insert gap marker at [chunk.start_sec, chunk.end_sec]
4. Set metadata.production_method = "segmented"
5. Write all output formats (JSON, TXT, SRT, VTT)
```
