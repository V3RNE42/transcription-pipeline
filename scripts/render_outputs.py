#!/usr/bin/env python3
"""
render_outputs.py — Phase 9: Output Rendering

Generates all output formats from a reconstructed transcript:
- Markdown (.md) with metadata header and temporal sections
- JSON (.json) with full segment structure
- SRT (.srt) subtitles
- VTT (.vtt) subtitles
- Plain text (.txt)
- Manifest (.json) with full pipeline metadata

Usage:
    python render_outputs.py <final_data_json> --workspace <dir> [--formats markdown,json,srt,vtt]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm."""
    total_ms = int(round(seconds * 1000))
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as WebVTT timestamp: HH:MM:SS.mmm."""
    total_ms = int(round(seconds * 1000))
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def render_markdown(
    transcript_data: dict[str, Any],
    template_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Render the transcript as formatted Markdown.

    Includes:
    - YAML-style metadata header (title, duration, word count, date)
    - Temporal sections with [HH:MM:SS] markers
    - Per-segment text with confidence overlay

    Args:
        transcript_data: Dict from recompose() with segments, full_text, etc.
        template_path: Optional path to a custom markdown template (unused in basic mode).
        output_path: If provided, write output to this file.

    Returns:
        The rendered markdown string.
    """
    segments = transcript_data.get("segments", [])
    full_text = transcript_data.get("full_text", "")
    total_duration = transcript_data.get("total_duration_sec", 0)
    word_count = transcript_data.get("word_count", 0)
    warnings = transcript_data.get("warnings", [])

    # Build metadata header
    duration_str = _format_srt_timestamp(total_duration).replace(",", ".")
    lines: list[str] = [
        "---",
        "title: Transcript",
        f"date: {datetime.now(timezone.utc).isoformat()}",
        f"duration: {duration_str}",
        f"duration_sec: {total_duration:.1f}",
        f"word_count: {word_count}",
        f"segment_count: {len(segments)}",
        "---",
        "",
        f"# Transcript",
        "",
        f"**Duration:** {duration_str} | **Words:** {word_count} | **Segments:** {len(segments)}",
        "",
    ]

    if warnings:
        lines.append("> **Warnings:**")
        for w in warnings:
            lines.append(f"> - {w}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Segment-by-segment rendering
    last_timestamp = -1
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "").strip()
        conf = seg.get("confidence", 1.0)
        source = seg.get("source_chunk", "")

        if not text:
            continue

        # Timestamp header every 30 seconds
        ts_marker = int(start // 30) * 30
        if ts_marker > last_timestamp:
            ts_formatted = _format_srt_timestamp(start).replace(",", ".")
            h = int(start // 3600)
            m = int((start % 3600) // 60)
            s = int(start % 60)
            if h > 0:
                clock_str = f"{h}:{m:02d}:{s:02d}"
            else:
                clock_str = f"{m:02d}:{s:02d}"
            lines.append(f"### [{clock_str}]")
            last_timestamp = ts_marker

        # Check for special markers
        is_failure = text.startswith("[MISSING TRANSCRIPTION:")
        is_silence = text.startswith("[SILENCE")

        if is_failure:
            lines.append(f"> **{text}**")
        elif is_silence:
            lines.append(f"*{text}*")
        elif conf < 0.5:
            lines.append(f"*{text}* _(low confidence: {conf:.0%})_")
        else:
            lines.append(text)

        lines.append("")

    result = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


def render_json(
    segments: list[dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Export transcript segments as structured JSON.

    Args:
        segments: List of segment dicts (from recompose()).
        output_path: If provided, write to this file.

    Returns:
        JSON string.
    """
    # Clean output: ensure all fields are JSON-safe
    clean_segments = []
    for seg in segments:
        clean_segments.append({
            "index": seg.get("segment_index", 0),
            "start_sec": seg.get("start", 0),
            "end_sec": seg.get("end", 0),
            "text": seg.get("text", ""),
            "confidence": seg.get("confidence", 1.0),
            "source_chunk": seg.get("source_chunk", ""),
            "words": [
                {
                    "word": w.get("word", ""),
                    "start_sec": w.get("start", 0),
                    "end_sec": w.get("end", 0),
                    "confidence": w.get("confidence", 1.0),
                }
                for w in seg.get("words", [])
            ],
        })

    result = json.dumps(clean_segments, indent=2, ensure_ascii=False)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


def render_srt(
    segments: list[dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Generate SRT subtitle format from segments.

    Args:
        segments: List of segment dicts with start/end/text.
        output_path: If provided, write to this file.

    Returns:
        SRT format string.
    """
    lines: list[str] = []
    subtitle_index = 1

    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "").strip()

        if not text:
            continue

        # Skip very short segments that might be glitches
        duration = end - start
        if duration < 0.5:
            continue

        # SRT entry
        lines.append(str(subtitle_index))
        lines.append(
            f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}"
        )
        lines.append(text)
        lines.append("")

        subtitle_index += 1

    result = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


def render_vtt(
    segments: list[dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Generate WebVTT subtitle format from segments.

    Args:
        segments: List of segment dicts with start/end/text.
        output_path: If provided, write to this file.

    Returns:
        WebVTT format string.
    """
    lines: list[str] = [
        "WEBVTT",
        f"Kind: captions",
        f"Date: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "").strip()

        if not text:
            continue

        duration = end - start
        if duration < 0.5:
            continue

        lines.append(
            f"{_format_vtt_timestamp(start)} --> {_format_vtt_timestamp(end)}"
        )
        lines.append(text)
        lines.append("")

    result = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


def render_plain_text(
    segments: list[dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    """Render segments as plain text without timestamps.

    Args:
        segments: List of segment dicts.
        output_path: If provided, write to this file.

    Returns:
        Plain text string.
    """
    texts: list[str] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if text:
            texts.append(text)

    result = " ".join(texts)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result)

    return result


def build_manifest(
    final_data: dict[str, Any],
    workspace: str,
    outputs: dict[str, str],
    parameters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the full pipeline manifest with all metadata.

    Args:
        final_data: Final recomposed transcript data.
        workspace: Pipeline workspace directory.
        outputs: Dict mapping format names to output file paths.
        parameters: Dict of pipeline input parameters.

    Returns:
        Dict representing the full manifest.
    """
    segments = final_data.get("segments", [])
    total_duration = final_data.get("total_duration_sec", 0)
    word_count = final_data.get("word_count", 0)
    warnings = final_data.get("warnings", [])

    # Calculate quality metrics
    transcribed_duration = sum(
        seg.get("end", 0) - seg.get("start", 0)
        for seg in segments
        if not seg.get("text", "").startswith("[MISSING")
    )

    coverage_ratio = (
        transcribed_duration / total_duration
        if total_duration > 0 else 0
    )

    confidence_scores = [
        seg.get("confidence", 1.0)
        for seg in segments
        if seg.get("confidence", 1.0) > 0
    ]
    mean_confidence = (
        sum(confidence_scores) / len(confidence_scores)
        if confidence_scores else 0
    )

    failure_segments = [
        seg for seg in segments
        if seg.get("text", "").startswith("[MISSING")
    ]

    quality_score = _calculate_quality_score(
        coverage_ratio=coverage_ratio,
        mean_confidence=mean_confidence,
        failure_count=len(failure_segments),
        total_segments=len(segments),
    )

    return {
        "manifest_version": "1.0.0",
        "job_id": Path(workspace).name if workspace != "." else "unknown",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "parameters": parameters or {},
        "technical_metadata": final_data.get("technical_metadata", {}),
        "outputs": outputs,
        "quality": {
            "score": round(quality_score, 4),
            "coverage_ratio": round(coverage_ratio, 4),
            "mean_confidence": round(mean_confidence, 4),
            "failure_segments": len(failure_segments),
            "total_segments": len(segments),
            "word_count": word_count,
            "total_duration_sec": total_duration,
        },
        "warnings": warnings,
        "summary": {
            "duration": _format_srt_timestamp(total_duration).replace(",", "."),
            "words": word_count,
            "segments": len(segments),
            "quality_score": round(quality_score, 2),
        },
    }


def _calculate_quality_score(
    coverage_ratio: float,
    mean_confidence: float,
    failure_count: int,
    total_segments: int,
) -> float:
    """Calculate aggregate quality score (0.0–1.0).

    Weighted combination of:
    - Coverage ratio (50%)
    - Mean confidence (30%)
    - Failure penalty (20%)
    """
    if total_segments == 0:
        return 0.0

    coverage_score = min(1.0, coverage_ratio / 0.95)
    confidence_score = min(1.0, mean_confidence / 0.6)
    failure_penalty = 1.0 - (failure_count / max(1, total_segments))

    score = (
        0.50 * coverage_score +
        0.30 * confidence_score +
        0.20 * failure_penalty
    )
    return max(0.0, min(1.0, score))


def render_outputs(
    final_data: dict[str, Any],
    workspace: str = ".",
    output_formats: Optional[list[str]] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """Orchestrate all output renders and return paths of generated files.

    Args:
        final_data: Dict from recompose() containing segments, full_text, etc.
        workspace: Pipeline workspace directory.
        output_formats: List of formats to generate. Supported:
            'markdown', 'json', 'srt', 'vtt', 'txt'.
            Defaults to ['markdown', 'json'].
        parameters: Optional dict of pipeline parameters for the manifest.

    Returns:
        Dict mapping format name to output file path.
    """
    if output_formats is None:
        output_formats = ["markdown", "json"]

    outputs_dir = Path(workspace) / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    segments = final_data.get("segments", [])
    generated: dict[str, str] = {}

    format_handlers = {
        "markdown": ("transcript_final.md", render_markdown),
        "json": ("transcript_segments.json", lambda s, p: render_json(s, p)),
        "srt": ("subtitles.srt", render_srt),
        "vtt": ("subtitles.vtt", render_vtt),
        "txt": ("transcript_final.txt", render_plain_text),
    }

    for fmt in output_formats:
        fmt = fmt.lower().strip()
        if fmt not in format_handlers:
            print(f"WARNING: Unknown output format '{fmt}'. Skipping.", file=sys.stderr)
            continue

        filename, handler = format_handlers[fmt]
        output_path = str(outputs_dir / filename)

        if fmt == "markdown":
            handler(final_data, None, output_path)
        elif fmt == "json":
            handler(segments, output_path)
        elif fmt in ("srt", "vtt", "txt"):
            handler(segments, output_path)

        generated[fmt] = output_path

    # Always generate manifest
    manifest = build_manifest(final_data, workspace, generated, parameters)
    manifest_path = str(outputs_dir / "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    generated["manifest"] = manifest_path

    return generated


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Render transcript outputs in multiple formats"
    )
    parser.add_argument(
        "final_data",
        help="Path to JSON file containing recomposed transcript data, or JSON string"
    )
    parser.add_argument(
        "--workspace", default=".",
        help="Pipeline workspace directory (default: current)"
    )
    parser.add_argument(
        "--formats", default="markdown,json",
        help="Comma-separated output formats (default: markdown,json)"
    )
    parser.add_argument(
        "--parameters", default=None,
        help="Optional JSON file or string with pipeline parameters"
    )
    parser.add_argument(
        "--list-formats", action="store_true",
        help="List available output formats and exit"
    )

    args = parser.parse_args()

    if args.list_formats:
        print("Available output formats:")
        print("  markdown  - Formatted markdown transcript (.md)")
        print("  json      - Structured segment JSON (.json)")
        print("  srt       - SubRip subtitles (.srt)")
        print("  vtt       - WebVTT subtitles (.vtt)")
        print("  txt       - Plain text transcript (.txt)")
        sys.exit(0)

    # Load final data
    final_path = Path(args.final_data)
    if final_path.exists():
        with open(final_path) as f:
            final_data = json.load(f)
    else:
        try:
            final_data = json.loads(args.final_data)
        except json.JSONDecodeError:
            print(
                f"ERROR: '{args.final_data}' is neither a valid file path "
                f"nor valid JSON",
                file=sys.stderr,
            )
            sys.exit(1)

    # Parse parameters
    parameters: Optional[dict[str, Any]] = None
    if args.parameters:
        param_path = Path(args.parameters)
        if param_path.exists():
            with open(param_path) as f:
                parameters = json.load(f)
        else:
            try:
                parameters = json.loads(args.parameters)
            except json.JSONDecodeError:
                print(f"WARNING: Could not parse parameters", file=sys.stderr)

    # Parse formats
    output_formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    try:
        outputs = render_outputs(
            final_data=final_data,
            workspace=args.workspace,
            output_formats=output_formats,
            parameters=parameters,
        )

        print("Generated outputs:")
        for fmt, path in outputs.items():
            status = "✓" if Path(path).exists() else "✗"
            size = Path(path).stat().st_size if Path(path).exists() else 0
            print(f"  {status} [{fmt}] {path} ({size:,} bytes)")

    except Exception as e:
        print(f"ERROR: Output rendering failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
