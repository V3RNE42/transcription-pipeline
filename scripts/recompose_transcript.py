#!/usr/bin/env python3
"""
recompose_transcript.py — Phase 8: Chronological Reconstruction

Merges per-chunk transcripts into a single, chronologically sorted,
deduplicated transcript. Handles:
- Normalizing chunk-relative timestamps to source-relative
- Reconciling overlapping segments between adjacent chunks
- Assembling the final structured transcript

Usage:
    python recompose_transcript.py <transcripts_json> [--overlap 5] [--chunking-enabled]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def normalize_timestamps(
    transcripts: list[dict[str, Any]],
    chunking_enabled: bool,
    overlap_sec: int = 5,
) -> list[dict[str, Any]]:
    """Convert chunk-relative timestamps to source-absolute and sort chronologically.

    Each transcript's segments have timestamps relative to their chunk start.
    This function adds the chunk's source-relative start offset to each segment.

    Args:
        transcripts: List of transcript dicts from transcribe_units.py.
            Each has: chunk_id, start_sec (source-relative), end_sec, segments.
        chunking_enabled: Whether the media was chunked.
        overlap_sec: Overlap between chunks in seconds (default: 5).

    Returns:
        List of segment dicts with source-absolute timestamps, sorted by start_sec.
    """
    if not transcripts:
        return []

    normalized: list[dict[str, Any]] = []

    for transcript in transcripts:
        chunk_start = transcript.get("start_sec", 0)
        segments = transcript.get("segments", [])

        for seg in segments:
            # Adjust timestamps: chunk-relative + chunk_start = source-absolute
            seg_start = chunk_start + seg.get("start", 0)
            seg_end = chunk_start + seg.get("end", 0)

            # Adjust word-level timestamps too
            words = seg.get("words", [])
            adjusted_words = []
            for w in words:
                adjusted_words.append({
                    "word": w.get("word", ""),
                    "start": chunk_start + w.get("start", 0),
                    "end": chunk_start + w.get("end", 0),
                    "confidence": w.get("confidence", 1.0),
                })

            normalized.append({
                "start": seg_start,
                "end": seg_end,
                "text": seg.get("text", ""),
                "confidence": seg.get("confidence", 1.0),
                "words": adjusted_words,
                "source_chunk": transcript.get("chunk_id", "unknown"),
            })

    # Sort by start_sec (stable sort preserves original order for ties)
    normalized.sort(key=lambda s: s["start"])
    return normalized


def reconcile_overlaps(
    segments: list[dict[str, Any]],
    overlap_sec: int = 5,
) -> list[dict[str, Any]]:
    """Remove or merge duplicate text in overlapping regions between chunks.

    Compares the tail of segment N with the head of segment N+1 for segments
    that originated from different chunks. If an overlap is detected:
    - Compares text similarity between overlapping words
    - Keeps the higher-confidence entry
    - Logs a warning when deduplication occurs

    Args:
        segments: List of normalized segment dicts (from normalize_timestamps).
        overlap_sec: Overlap duration in seconds (default: 5).

    Returns:
        Deduplicated segment list.
    """
    if len(segments) < 2:
        return segments

    result: list[dict[str, Any]] = []
    warnings: list[str] = []
    i = 0

    while i < len(segments):
        current = segments[i]
        current_start = current["start"]
        current_end = current["end"]
        current_text = current["text"].strip()
        current_chunk = current.get("source_chunk", "")
        current_conf = current.get("confidence", 1.0)

        # Look ahead for overlapping segments from different chunks
        j = i + 1
        merged = False
        while j < len(segments):
            next_seg = segments[j]
            next_start = next_seg["start"]
            next_end = next_seg["end"]
            next_text = next_seg["text"].strip()
            next_chunk = next_seg.get("source_chunk", "")
            next_conf = next_seg.get("confidence", 1.0)

            # Only reconcile if from different chunks and overlapping in time
            if next_chunk == current_chunk:
                break  # Same chunk — segments shouldn't overlap internally

            if next_start >= current_end:
                break  # No overlap

            # Overlap detected — reconcile
            overlap_start = next_start
            overlap_end = min(current_end, next_end)
            overlap_duration = overlap_end - overlap_start

            # Only reconcile if overlap is plausible (within overlap_sec + margin)
            if overlap_duration > overlap_sec * 2:
                # Large overlap — could be different content; keep both
                break

            # Check text similarity in the overlap zone
            similarity = _text_similarity(current_text, next_text)

            if similarity > 0.5:
                # High similarity — likely duplicate; keep higher confidence
                if current_conf >= next_conf:
                    # Keep current, skip next's overlap region
                    if next_end > current_end:
                        # Truncate next segment to only cover non-overlapping part
                        next_seg = {
                            **next_seg,
                            "start": current_end,
                            "text": f"[...] {next_text}" if next_text else next_text,
                        }
                        segments[j] = next_seg
                    # else: next is fully overlapped — skip it
                    warnings.append(
                        f"Overlap reconciled: '{current_chunk}' overlaps '{next_chunk}' "
                        f"({overlap_duration:.1f}s, similarity={similarity:.2f}), "
                        f"kept '{current_chunk}' (conf={current_conf})"
                    )
                    result.append(current)
                    # Skip to next non-overlapping segment
                    i = j
                    merged = True
                    break
                else:
                    # Next segment has higher confidence — keep its overlap content
                    warnings.append(
                        f"Overlap reconciled: '{next_chunk}' overlaps '{current_chunk}' "
                        f"({overlap_duration:.1f}s, similarity={similarity:.2f}), "
                        f"kept '{next_chunk}' (conf={next_conf})"
                    )
                    result.append(next_seg)
                    i = j + 1
                    merged = True
                    break
            else:
                # Low similarity — content might differ (e.g., speaker change);
                # keep both but mark the overlap
                warnings.append(
                    f"Overlap detected with low similarity ({similarity:.2f}) — "
                    f"keeping both segments from '{current_chunk}' and '{next_chunk}'"
                )
                break

        if not merged:
            result.append(current)
            i += 1

    # Log warnings (attached to result for the assembler)
    result_meta = getattr(reconcile_overlaps, "_warnings", [])
    reconcile_overlaps._warnings = warnings  # type: ignore[attr-defined]

    return result


def _text_similarity(text1: str, text2: str) -> float:
    """Compute simple word-overlap similarity between two text strings.

    Returns a score between 0.0 (no overlap) and 1.0 (identical word sets).
    Uses Jaccard similarity on word sets for efficiency.
    """
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def assemble_transcript(segments: list[dict[str, Any]]) -> str:
    """Concatenate segment texts in chronological order into a full transcript.

    Inserts timestamp headers at natural boundaries (every ~30 seconds or
    when a gap of >5 seconds exists) for readability.

    Args:
        segments: List of sorted, deduplicated segment dicts.

    Returns:
        Full transcript text with temporal markers.
    """
    if not segments:
        return ""

    lines: list[str] = []
    last_timestamp_marker = -30  # Force first header
    last_end_time = 0.0

    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "").strip()

        if not text:
            continue

        # Insert timestamp header if gap or periodic marker
        if (start - last_timestamp_marker >= 30) or (start - last_end_time > 5):
            ts = _format_ts_clock(start)
            lines.append(f"\n**[{ts}]**")
            last_timestamp_marker = start

        lines.append(text)
        last_end_time = end

    return "\n\n".join(lines)


def _format_ts_clock(seconds: float) -> str:
    """Format seconds as HH:MM:SS for transcript headers."""
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def recompose(
    transcripts: list[dict[str, Any]],
    chunking_enabled: bool = False,
    overlap_sec: int = 5,
) -> dict[str, Any]:
    """Orchestrate the full reconstruction: normalize → reconcile → assemble.

    Args:
        transcripts: List of per-chunk transcript dicts.
        chunking_enabled: Whether chunking was used.
        overlap_sec: Overlap between chunks in seconds.

    Returns:
        Dict with:
            - segments: Final sorted, deduplicated segment list.
            - full_text: Assembled full transcript text.
            - total_duration_sec: Total duration covered.
            - warnings: List of reconciliation warnings.
            - segment_count: Number of segments.
            - word_count: Total word count.
    """
    # Phase 1: Normalize timestamps
    normalized = normalize_timestamps(transcripts, chunking_enabled, overlap_sec)

    if not normalized:
        return {
            "segments": [],
            "full_text": "",
            "total_duration_sec": 0,
            "warnings": ["No segments to reconstruct"],
            "segment_count": 0,
            "word_count": 0,
        }

    # Phase 2: Reconcile overlaps (only if chunking was enabled)
    if chunking_enabled:
        reconciled = reconcile_overlaps(normalized, overlap_sec)
        warnings = getattr(reconcile_overlaps, "_warnings", [])
    else:
        reconciled = normalized
        warnings = []

    # Get total duration
    total_duration = max(
        (seg["end"] for seg in reconciled),
        default=0,
    )

    # Phase 3: Assemble
    full_text = assemble_transcript(reconciled)

    # Calculate word count
    word_count = sum(
        len(seg.get("text", "").split())
        for seg in reconciled
    )

    # Re-number segments sequentially
    for idx, seg in enumerate(reconciled):
        seg["segment_index"] = idx + 1

    return {
        "segments": reconciled,
        "full_text": full_text,
        "total_duration_sec": total_duration,
        "warnings": warnings,
        "segment_count": len(reconciled),
        "word_count": word_count,
    }


def load_transcripts(transcripts_data: Any) -> list[dict[str, Any]]:
    """Load transcripts from various input formats.

    Accepts:
    - A list of transcript dicts
    - A JSON file path containing a list of transcript dicts
    - A JSON file path containing a dict with a 'transcripts' key
    - A JSON string

    Args:
        transcripts_data: List of dicts, file path (str/Path), or JSON string.

    Returns:
        List of transcript dicts.
    """
    if isinstance(transcripts_data, list):
        return transcripts_data

    if isinstance(transcripts_data, (str, Path)):
        path = Path(transcripts_data)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
        else:
            try:
                data = json.loads(str(transcripts_data))
            except json.JSONDecodeError:
                raise ValueError(
                    f"Cannot parse transcripts: '{transcripts_data}' is not a "
                    f"valid file path or JSON string"
                )

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "transcripts" in data:
                return data["transcripts"]
            # Also try common keys
            for key in ("chunks", "results", "segments"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Might be a single transcript
            if "chunk_id" in data:
                return [data]
        raise ValueError(
            f"Cannot extract transcript list from input. "
            f"Expected a list of transcript dicts, got {type(data).__name__}"
        )

    raise TypeError(f"Unsupported input type: {type(transcripts_data).__name__}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Reconstruct full transcript from per-chunk transcripts"
    )
    parser.add_argument(
        "transcripts_input",
        help="Path to JSON file containing list of transcript dicts, or JSON string"
    )
    parser.add_argument(
        "--overlap", type=int, default=5,
        help="Overlap between chunks in seconds (default: 5)"
    )
    parser.add_argument(
        "--chunking-enabled", action="store_true",
        help="Whether chunking was used (enables overlap reconciliation)"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output path for recomposed JSON (default: stdout)"
    )

    args = parser.parse_args()

    try:
        transcripts = load_transcripts(args.transcripts_input)
    except (ValueError, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if not transcripts:
        print("ERROR: No transcripts found in input", file=sys.stderr)
        sys.exit(1)

    try:
        result = recompose(
            transcripts,
            chunking_enabled=args.chunking_enabled,
            overlap_sec=args.overlap,
        )
    except Exception as e:
        print(f"ERROR: Reconstruction failed: {e}", file=sys.stderr)
        sys.exit(1)

    json_output = json.dumps(result, default=str, indent=2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(json_output)
        print(f"Recomposed transcript saved to: {args.output}")
    else:
        print(json_output)


if __name__ == "__main__":
    main()
