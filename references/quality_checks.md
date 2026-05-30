# Quality Checks Reference

> Quality gates that validate pipeline output before it is considered complete.
> Each check has a severity level that determines whether it blocks the pipeline
> entirely, produces a warning, or allows a partial pass.

---

## Severity Levels

| Level | Effect | Symbol |
|-------|--------|--------|
| **blocking** | Pipeline fails — no output artifacts are considered final | 🔴 |
| **partial blocking** | Pipeline produces output but marks gaps — status = `partial_pass` | 🟡 |
| **warning** | Non-blocking issue — status = `passed` but `warnings` list populated | 🟠 |

---

## Check 1: Temporal Coverage (🔴 blocking)

### Description
Verifies that the assembled transcript covers the full duration of the source
audio, from `0.0s` to `media_info.duration_sec`. Any uncovered region counts
as a gap — but only unforced gaps are blocking. Gaps from `failed_final`
chunks are expected and produce a `partial_pass` (see Check 5).

### What it validates
- First segment starts at or near `0.0s` (tolerance: ±0.5s)
- Last segment ends at or near `media_info.duration_sec` (tolerance: ±1.0s)
- No unexpected gaps (gaps not explained by `failed_final` chunks)

### Programmatic check
```python
def check_temporal_coverage(manifest, transcript):
    first_seg_start = transcript["segments"][0]["start"]
    last_seg_end = transcript["segments"][-1]["end"]
    failed_ranges = [
        (c["start_sec"], c["end_sec"])
        for c in manifest["chunks"]
        if c["status"] == "failed_final"
    ]

    issues = []
    if first_seg_start > 0.5:
        issues.append(f"First segment starts at {first_seg_start}s (expected ~0.0s)")

    if last_seg_end < manifest["media_info"]["duration_sec"] - 1.0:
        issues.append(
            f"Last segment ends at {last_seg_end}s, "
            f"expected {manifest['media_info']['duration_sec']}s"
        )

    # Check for gaps outside failed chunk ranges
    for i in range(len(transcript["segments"]) - 1):
        gap_start = transcript["segments"][i]["end"]
        gap_end = transcript["segments"][i + 1]["start"]
        gap_duration = gap_end - gap_start

        if gap_duration > 2.0:  # 2-second tolerance for natural pauses
            # Is this gap within a known failed chunk range?
            in_failed_range = any(
                fs <= gap_start and fe >= gap_end
                for fs, fe in failed_ranges
            )
            if not in_failed_range:
                issues.append(
                    f"Unexpected gap of {gap_duration:.1f}s "
                    f"between {gap_start:.1f}s and {gap_end:.1f}s"
                )

    return {
        "status": "failed" if issues else "passed",
        "detail": "; ".join(issues) if issues else
                  f"100% coverage ({first_seg_start:.1f}s to {last_seg_end:.1f}s)"
    }
```

### On failure
- **Pipeline status**: `failed`
- **Action required**: Investigate chunking or assembly logic for the
  specific time range. Check if chunk extraction produced correct files.

---

## Check 2: Segment Order (🔴 blocking)

### Description
Verifies that all segments in the assembled transcript are in strict
chronological order. Out-of-order segments indicate a chunk assembly bug
that would produce an unusable transcript.

### What it validates
- Each segment's `start` timestamp is ≥ the previous segment's `start`
- Chunk ordering matches the chunk index sequence

### Programmatic check
```python
def check_segment_order(transcript):
    segments = transcript["segments"]
    issues = []

    for i in range(1, len(segments)):
        if segments[i]["start"] < segments[i - 1]["start"]:
            issues.append(
                f"Segment {i} starts at {segments[i]['start']:.1f}s "
                f"which is before segment {i-1} at {segments[i-1]['start']:.1f}s"
            )

    return {
        "status": "failed" if issues else "passed",
        "detail": "; ".join(issues) if issues else
                  f"All {len(segments)} segments in chronological order"
    }
```

### On failure
- **Pipeline status**: `failed`
- **Action required**: Check chunk sorting and assembly logic. Ensure chunks
  are sorted by `chunk_id` (which implies chronological order) and not by
  completion order.

---

## Check 3: Final Artifacts Exist (🔴 blocking)

### Description
Verifies that all expected output files exist at their declared paths in the
manifest. Missing artifacts mean the assembly or export stage did not complete.

### What it validates
- `outputs.assembled_transcript_raw.json` exists and is valid JSON
- `outputs.assembled_transcript_cleaned.json` exists and is valid JSON
- `outputs.assembled_transcript_txt` exists and is non-empty
- `outputs.assembled_transcript_srt` exists and is valid SRT format
- `outputs.assembled_transcript_vtt` exists and is valid VTT format
- `outputs.chunking_report.json` exists and is valid JSON
- `outputs.quality_report.json` exists and is valid JSON

### Programmatic check
```python
import os, json

def check_final_artifacts(manifest):
    expected = [
        "assembled_transcript_raw",
        "assembled_transcript_cleaned",
        "assembled_transcript_txt",
        "assembled_transcript_srt",
        "assembled_transcript_vtt",
        "chunking_report",
        "quality_report",
    ]
    missing = []

    for key in expected:
        path = manifest["outputs"].get(key)
        if not path:
            missing.append(f"{key}: no path in manifest")
            continue
        if not os.path.exists(path):
            missing.append(f"{key}: {path} not found")
            continue
        if path.endswith(".txt") and os.path.getsize(path) == 0:
            missing.append(f"{key}: {path} is empty")

    # Validate JSON formats
    for json_key in ["assembled_transcript_raw", "assembled_transcript_cleaned",
                     "chunking_report", "quality_report"]:
        path = manifest["outputs"].get(json_key)
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    json.load(f)
            except json.JSONDecodeError:
                missing.append(f"{json_key}: {path} is not valid JSON")

    return {
        "status": "failed" if missing else "passed",
        "detail": "; ".join(missing) if missing else
                  "All 7 expected output files found and valid"
    }
```

### On failure
- **Pipeline status**: `failed`
- **Action required**: Re-run the assembly/export stage. Check disk space and
  write permissions.

---

## Check 4: Overlap Duplicates (🟠 warning)

### Description
Checks for duplicate text segments in overlap regions that were not
successfully reconciled. Duplicates affect readability but don't make the
transcript unusable.

### What it validates
- No two consecutive segments with identical or near-identical text within
  an overlap window
- Word-level timestamp overlap does not exceed the expected overlap range

### Programmatic check
```python
def check_overlap_duplicates(manifest, transcript):
    segments = transcript["segments"]
    overlap_sec = manifest["chunking"].get("chunk_overlap_sec", 0)
    duplicates_found = []

    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]

        # Check time overlap (segment start before previous segment ends)
        if curr["start"] < prev["end"]:
            # Compute text similarity (simple word overlap ratio)
            prev_words = set(prev["text"].lower().split())
            curr_words = set(curr["text"].lower().split())
            if prev_words and curr_words:
                overlap_ratio = len(prev_words & curr_words) / len(prev_words | curr_words)
                if overlap_ratio > 0.5:
                    duplicates_found.append(
                        f"Segments {i-1} and {i}: {overlap_ratio:.0%} word overlap "
                        f"at {prev['start']:.1f}s–{prev['end']:.1f}s / "
                        f"{curr['start']:.1f}s–{curr['end']:.1f}s"
                    )

    return {
        "status": "warning" if duplicates_found else "passed",
        "detail": "; ".join(duplicates_found) if duplicates_found else
                  "No duplicate segments found in overlap regions"
    }
```

### On failure
- **Effect**: Warning added to `quality.warnings`
- **Pipeline status**: Still `passed` (unless combined with other issues)
- **Action recommended**: Review the reconciliation algorithm. The overlap
  dedup logic may need tuning for the specific overlap duration used.

---

## Check 5: Per-Chunk Failures (🟡 partial blocking)

### Description
Checks how many chunks have status `failed_final`. If some chunks failed but
most succeeded, the pipeline can produce a partial result with gaps marked.

### What it validates
- Count of `failed_final` chunks
- Ratio of failed to total chunks
- No `failed_final` chunks if chunking was disabled (all-or-nothing)

### Programmatic check
```python
def check_per_chunk_failures(manifest):
    chunks = manifest["chunks"]
    total = len(chunks)
    failed = [c for c in chunks if c["status"] == "failed_final"]
    partial_pass = [c for c in chunks if c["status"] == "failed_retryable"]
    failed_count = len(failed)
    partial_count = len(partial_pass)

    if not manifest["chunking"]["enabled"] and failed_count > 0:
        return {
            "status": "failed",
            "detail": "Chunking disabled but chunk status shows failures — inconsistent"
        }

    if failed_count == 0 and partial_count == 0:
        return {
            "status": "passed",
            "detail": f"0 failed chunks out of {total}"
        }

    ratio = failed_count / total if total > 0 else 0

    if ratio > 0.5:
        return {
            "status": "failed",
            "detail": f"{failed_count}/{total} chunks failed ({ratio:.0%}) — exceeds 50% threshold"
        }

    # Build detail about each failed chunk
    failed_details = []
    for c in failed:
        failed_details.append(
            f"chunk {c['chunk_id']} ({c['start_sec']:.0f}s–{c['end_sec']:.0f}s): "
            f"{c['last_error']} after {c['attempts']} attempts"
        )
    for c in partial_pass:
        failed_details.append(
            f"chunk {c['chunk_id']} ({c['start_sec']:.0f}s–{c['end_sec']:.0f}s): "
            f"retryable ({c['attempts']} attempts so far)"
        )

    return {
        "status": "partial_pass",
        "detail": "; ".join(failed_details)
    }
```

### Thresholds

| Failed Ratio | Result | Action |
|-------------|--------|--------|
| 0% | `passed` | All chunks transcribed successfully |
| >0% to ≤50% | `partial_pass` | Pipeline continues, gaps marked in output |
| >50% | `failed` | Pipeline aborts — too many failures |

### On partial_pass
- **Pipeline status**: `partial_pass`
- **Output**: Transcript produced with `[GAP]` markers at failed chunk locations
- **Action required**: Investigate failure patterns (API errors, timeouts, file
  corruption). Retry failed chunks individually if needed.

---

## Check 6: Readability (🟠 warning)

### Description
Evaluates the quality of the assembled transcript based on word-level
confidence scores and text heuristics. Low-confidence or gibberish segments
are flagged but don't block the pipeline.

### What it validates
- Average word confidence across all segments (if available)
- Segments with average confidence below threshold
- Segments with unusually short duration (possible hallucination)
- Segments with abnormally high character-per-second rate

### Programmatic check
```python
def check_readability(transcript):
    segments = transcript.get("segments", [])
    if not segments:
        return {"status": "warning", "detail": "No segments to evaluate"}

    low_conf_segments = []
    fast_segments = []
    short_segments = []
    total_words = 0
    total_confidence = 0.0
    words_with_conf = 0

    for i, seg in enumerate(segments):
        text = seg.get("text", "")
        duration = seg.get("end", 0) - seg.get("start", 0)
        word_count = len(text.split())

        total_words += word_count

        # Check per-word confidence
        for word in seg.get("words", []):
            conf = word.get("confidence") or word.get("probability")
            if conf is not None:
                total_confidence += conf
                words_with_conf += 1

        # Check for low confidence in segment
        seg_words = seg.get("words", [])
        if seg_words:
            avg_seg_conf = sum(
                w.get("confidence") or w.get("probability") or 0
                for w in seg_words
            ) / len(seg_words)
            if avg_seg_conf < 0.5:
                low_conf_segments.append(
                    f"Segment {i} at {seg['start']:.1f}s: "
                    f"avg confidence {avg_seg_conf:.2f}"
                )

        # Check for abnormally fast speech (>10 chars/sec)
        if duration > 0:
            cps = len(text) / duration
            if cps > 15:
                fast_segments.append(
                    f"Segment {i} at {seg['start']:.1f}s: "
                    f"{cps:.1f} chars/sec ({len(text)} chars in {duration:.1f}s)"
                )

        # Check for very short segments (possible hallucination)
        if word_count <= 2 and duration > 0:
            short_segments.append(
                f"Segment {i} at {seg['start']:.1f}s: "
                f"only {word_count} word(s) in {duration:.1f}s"
            )

    warnings = []
    avg_conf = total_confidence / words_with_conf if words_with_conf > 0 else None

    if avg_conf is not None:
        if avg_conf < 0.8:
            warnings.append(f"Low average confidence: {avg_conf:.2f}")
        else:
            readability_detail = f"Average word confidence: {avg_conf:.2f}"
    else:
        readability_detail = "No word-level confidence data available"

    if low_conf_segments:
        warnings.extend(low_conf_segments)
    if fast_segments:
        warnings.extend(fast_segments[:3])  # Cap at 3 examples
    if short_segments:
        warnings.extend(short_segments[:3])

    return {
        "status": "warning" if warnings else "passed",
        "detail": "; ".join(warnings) if warnings else
                  (readability_detail + ", no low-confidence segments")
    }
```

### On warning
- **Effect**: Warnings added to `quality.warnings`
- **Pipeline status**: Still `passed`
- **Action recommended**: For low confidence, consider a different model or
  language. For fast speech, verify audio quality. Short segments may be
  filtering artifacts.

---

## Quality Report JSON Structure

```json
{
  "pipeline_id": "uuid-v4",
  "checked_at": "2026-05-16T21:05:31Z",
  "overall_status": "passed",
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
      "detail": "All 7 expected output files found and valid"
    },
    "overlap_duplicates": {
      "status": "warning",
      "detail": "2 duplicate segments found in overlap regions, auto-reconciled"
    },
    "per_chunk_failures": {
      "status": "partial_pass",
      "detail": "chunk chunk_001 (285s–585s): whisper_api_timeout after 3 attempts"
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
    "1 chunk failed permanently (chunk_001), gap from 285s to 585s"
  ]
}
```

---

## Quick Reference: What To Do On Each Failure

| Check | Severity | Pipeline Result | What To Do |
|-------|----------|----------------|------------|
| Temporal Coverage | 🔴 blocking | `failed` | Fix chunking or assembly logic for the uncovered range |
| Segment Order | 🔴 blocking | `failed` | Fix chunk sort order in assembly stage |
| Final Artifacts Exist | 🔴 blocking | `failed` | Re-run assembly; check disk/write permissions |
| Overlap Duplicates | 🟠 warning | `passed` (with warning) | Tune reconciliation dedup algorithm |
| Per-Chunk Failures (>50%) | 🔴 blocking | `failed` | Diagnose API/model errors; retry with different config |
| Per-Chunk Failures (≤50%) | 🟡 partial | `partial_pass` | Output has gaps; retry failed chunks individually |
| Readability | 🟠 warning | `passed` (with warning) | Review low-confidence segments; consider better model |
