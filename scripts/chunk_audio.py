#!/usr/bin/env python3
"""
chunk_audio.py — Phase 4 & 5: Chunking Decision and Audio Chunking

Decides whether audio should be chunked based on duration, policy, and engine
limits, then creates deterministic, overlapping audio chunks.

Chunk naming follows the deterministic convention:
    chunk_NNNN_HH-MM-SS_HH-MM-SS.wav

Usage:
    python chunk_audio.py <audio_path> [--workspace <dir>] [--target-duration 600] [--overlap 5] [--policy auto]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def decide_chunking(
    media_info: dict[str, Any],
    policy: str = "auto",
    target_duration_sec: int = 600,
    engine_limits: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Decide whether chunking is needed based on media metadata and policy.

    Args:
        media_info: Dict from inspect_media() containing at least 'duration_sec', 'risks'.
        policy: Chunking policy — 'auto', 'always', or 'never'.
        target_duration_sec: Target duration per chunk in seconds (default: 600 = 10 min).
        engine_limits: Optional dict with STT engine constraints, e.g.
            {'max_duration_sec': 1800, 'max_file_size_mb': 500}.

    Returns:
        Dict with keys:
            - enabled: Whether chunking should be performed (bool).
            - reason: Human-readable justification (str).
            - target_duration_sec: The chunk duration to use (int).
            - overlap_sec: Overlap between chunks in seconds (int).

    Raises:
        ValueError: If policy is unknown or media_info lacks duration_sec.
    """
    valid_policies = {"auto", "always", "never"}
    if policy not in valid_policies:
        raise ValueError(f"Unknown policy '{policy}'. Must be one of: {', '.join(sorted(valid_policies))}")

    duration = media_info.get("duration_sec")
    if duration is None:
        raise ValueError("media_info must contain 'duration_sec'")

    engine_limits = engine_limits or {}
    max_duration = engine_limits.get("max_duration_sec", 3600)
    max_file_size = engine_limits.get("max_file_size_mb", float("inf"))
    # Convert to bytes
    max_file_size_bytes = max_file_size * 1024 * 1024 if max_file_size != float("inf") else float("inf")

    risks = media_info.get("risks", [])
    size_bytes = media_info.get("size_bytes", 0)

    # Decision logic
    enabled = False
    reason = "No chunking needed"

    if policy == "always":
        enabled = True
        reason = f"Policy set to '{policy}'"
    elif policy == "never":
        enabled = False
        reason = f"Policy set to '{policy}'"
    elif policy == "auto":
        # Auto-detect: chunk if long, large, or risky
        if duration > target_duration_sec:
            enabled = True
            reason = (
                f"Duration ({duration:.0f}s) exceeds target chunk duration "
                f"({target_duration_sec}s)"
            )
        elif size_bytes > max_file_size_bytes:
            enabled = True
            reason = (
                f"File size ({size_bytes / 1024 / 1024:.1f} MB) exceeds engine "
                f"limit ({max_file_size:.0f} MB)"
            )
        elif duration > max_duration:
            enabled = True
            reason = (
                f"Duration ({duration:.0f}s) exceeds engine limit "
                f"({max_duration}s)"
            )
        elif any("chunking" in risk.lower() for risk in risks):
            enabled = True
            reason = f"Risk detected: {[r for r in risks if 'chunking' in r.lower()][0]}"
        # If file is very short (< 60s), never chunk
        elif duration < 60:
            enabled = False
            reason = f"Short file ({duration:.0f}s) — no chunking needed"

    # Sanity: don't chunk if total duration is less than 2 chunk durations
    if enabled and duration < target_duration_sec * 1.5:
        enabled = False
        reason = (
            f"Duration ({duration:.0f}s) is less than 1.5x target chunk "
            f"({target_duration_sec}s) — no chunking needed"
        )

    return {
        "enabled": enabled,
        "reason": reason,
        "target_duration_sec": target_duration_sec,
        "overlap_sec": 5,  # Fixed 5s overlap for safety
    }


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH-MM-SS (for filenames)."""
    total_sec = int(round(seconds))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}-{m:02d}-{s:02d}"


def _create_chunks_ffmpeg(
    audio_path: str,
    target_duration_sec: int,
    overlap_sec: int,
    chunks_dir: str,
    total_duration: float,
) -> list[dict[str, Any]]:
    """Create overlapping audio chunks using ffmpeg.

    Uses individual ffmpeg calls per chunk for precise control over
    overlap boundaries and naming.
    """
    chunks: list[dict[str, Any]] = []
    chunk_index = 1
    start_sec = 0.0

    while start_sec < total_duration:
        end_sec = min(start_sec + target_duration_sec, total_duration)
        chunk_duration = end_sec - start_sec

        start_ts = _format_timestamp(start_sec)
        end_ts = _format_timestamp(end_sec)
        chunk_filename = f"chunk_{chunk_index:04d}_{start_ts}_{end_ts}.wav"
        chunk_path = os.path.join(chunks_dir, chunk_filename)

        overlap_before = overlap_sec if chunk_index > 1 else 0
        overlap_after = (
            overlap_sec
            if end_sec < total_duration and (end_sec + overlap_sec) < total_duration
            else 0
        )

        # Calculate actual overlap: end_sec accounts for overlap
        actual_end = min(end_sec + overlap_after, total_duration)
        actual_duration = actual_end - start_sec

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start_sec),
            "-i", audio_path,
            "-t", str(actual_duration),
            "-c", "copy",  # Copy codec (fast, no re-encode)
            "-avoid_negative_ts", "make_zero",
            chunk_path,
        ]

        try:
            subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=600, check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            # Fallback: re-encode with pcm_s16le
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(start_sec),
                "-i", audio_path,
                "-t", str(actual_duration),
                "-f", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                chunk_path,
            ]
            try:
                subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=600, check=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                chunk_path = None

        chunk_info: dict[str, Any] = {
            "chunk_id": f"chunk_{chunk_index:04d}",
            "chunk_path": chunk_path if chunk_path and Path(chunk_path).exists() else None,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration_sec": actual_duration,
            "overlap_before_sec": overlap_before,
            "overlap_after_sec": overlap_after,
            "status": "ready" if (chunk_path and Path(chunk_path).exists()) else "failed",
            "attempts": 0,
            "transcript_path": None,
        }
        chunks.append(chunk_info)

        # Next chunk starts with overlap
        start_sec = end_sec
        chunk_index += 1

    return chunks


def _create_single_chunk_symlink(
    audio_path: str,
    total_duration: float,
    chunks_dir: str,
) -> list[dict[str, Any]]:
    """Create a single chunk as a symlink (no chunking needed)."""
    end_ts = _format_timestamp(total_duration)
    chunk_filename = f"chunk_0001_00-00-00_{end_ts}.wav"
    chunk_path = os.path.join(chunks_dir, chunk_filename)

    # Symlink or copy
    try:
        os.symlink(os.path.abspath(audio_path), chunk_path)
    except OSError:
        # Fallback to copy
        import shutil
        shutil.copy2(audio_path, chunk_path)

    return [
        {
            "chunk_id": "chunk_0001",
            "chunk_path": chunk_path,
            "start_sec": 0.0,
            "end_sec": total_duration,
            "duration_sec": total_duration,
            "overlap_before_sec": 0,
            "overlap_after_sec": 0,
            "status": "ready",
            "attempts": 0,
            "transcript_path": None,
        }
    ]


def create_audio_chunks(
    audio_path: str,
    target_duration_sec: int = 600,
    overlap_sec: int = 5,
    workspace: str = ".",
    total_duration: Optional[float] = None,
    chunking_enabled: bool = True,
) -> list[dict[str, Any]]:
    """Split prepared audio into deterministic, overlapping chunks.

    Args:
        audio_path: Path to the prepared WAV audio file.
        target_duration_sec: Target duration per chunk in seconds (default: 600).
        overlap_sec: Overlap between adjacent chunks in seconds (default: 5).
        workspace: Working directory for pipeline output.
        total_duration: Total audio duration in seconds. Auto-detected if None.
        chunking_enabled: If False, create a single chunk (symlink).

    Returns:
        List of chunk info dicts, each containing:
            - chunk_id: Deterministic chunk identifier.
            - chunk_path: Absolute path to the chunk WAV file (None if failed).
            - start_sec: Start time relative to source (float).
            - end_sec: End time relative to source (float).
            - duration_sec: Actual chunk duration including overlap (float).
            - overlap_before_sec: Leading overlap seconds (float).
            - overlap_after_sec: Trailing overlap seconds (float).
            - status: 'ready', 'failed', or 'pending'.
            - attempts: Retry count (int).
            - transcript_path: Path to transcript file or None (str | None).

    Raises:
        FileNotFoundError: If audio_path does not exist.
        ValueError: If the audio file is empty.
    """
    audio = Path(audio_path)
    if not audio.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if audio.stat().st_size == 0:
        raise ValueError(f"Audio file is empty: {audio_path}")

    # Auto-detect duration if not provided
    if total_duration is None:
        # Try to get duration from WAV header
        import wave
        try:
            with wave.open(str(audio), "rb") as wf:
                total_duration = wf.getnframes() / wf.getframerate()
        except (wave.Error, OSError):
            raise ValueError(
                "Cannot determine audio duration. Provide total_duration or "
                "use a valid WAV file."
            )

    chunks_dir = Path(workspace) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    if not chunking_enabled:
        return _create_single_chunk_symlink(str(audio), total_duration, str(chunks_dir))

    return _create_chunks_ffmpeg(
        str(audio), target_duration_sec, overlap_sec,
        str(chunks_dir), total_duration,
    )


def validate_coverage(
    chunks: list[dict[str, Any]],
    total_duration: float,
) -> list[str]:
    """Validate that chunk coverage spans the full audio duration.

    Returns a list of warning strings. Empty list = full coverage.
    """
    warnings: list[str] = []
    if not chunks:
        warnings.append("No chunks created")
        return warnings

    # Check first chunk starts at 0
    if chunks[0]["start_sec"] != 0.0:
        warnings.append(
            f"First chunk does not start at 0s (starts at {chunks[0]['start_sec']}s)"
        )

    # Check last chunk covers end
    last = chunks[-1]
    if last["end_sec"] < total_duration:
        warnings.append(
            f"Last chunk ends at {last['end_sec']}s but audio is {total_duration}s "
            f"— gap of {total_duration - last['end_sec']:.1f}s"
        )

    # Check for gaps between chunks
    for i in range(len(chunks) - 1):
        cur = chunks[i]
        nxt = chunks[i + 1]
        if nxt["start_sec"] > cur["end_sec"]:
            warnings.append(
                f"Gap between chunk {i+1} (ends {cur['end_sec']}s) and "
                f"chunk {i+2} (starts {nxt['start_sec']}s): "
                f"{nxt['start_sec'] - cur['end_sec']:.1f}s uncovered"
            )

    # Check for failed chunks
    failed = [c for c in chunks if c["status"] == "failed"]
    if failed:
        warnings.append(
            f"{len(failed)} chunk(s) failed to create: "
            f"{', '.join(c['chunk_id'] for c in failed)}"
        )

    return warnings


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Create audio chunks for transcription pipeline"
    )
    parser.add_argument("audio_path", help="Path to the prepared audio WAV file")
    parser.add_argument(
        "--workspace", default=".",
        help="Working directory (default: current)"
    )
    parser.add_argument(
        "--target-duration", type=int, default=600,
        help="Target chunk duration in seconds (default: 600)"
    )
    parser.add_argument(
        "--overlap", type=int, default=5,
        help="Overlap between chunks in seconds (default: 5)"
    )
    parser.add_argument(
        "--total-duration", type=float, default=None,
        help="Total audio duration in seconds (auto-detect if not given)"
    )
    parser.add_argument(
        "--chunking-enabled", action="store_true", default=True,
        help="Enable chunking (default: true)"
    )
    parser.add_argument(
        "--policy", default="auto",
        choices=["auto", "always", "never"],
        help="Chunking policy (default: auto)"
    )
    parser.add_argument(
        "--media-info", default=None,
        help="JSON string or path to JSON file with media info"
    )

    args = parser.parse_args()

    # Parse media_info if provided
    media_info = {"duration_sec": args.total_duration, "risks": []}
    if args.media_info:
        if Path(args.media_info).exists():
            with open(args.media_info) as f:
                media_info = json.load(f)
        else:
            try:
                media_info = json.loads(args.media_info)
            except json.JSONDecodeError:
                pass

    try:
        # Decide chunking
        decision = decide_chunking(
            media_info,
            policy=args.policy,
            target_duration_sec=args.target_duration,
        )
        print(f"Chunking decision: {'ENABLED' if decision['enabled'] else 'DISABLED'}")
        print(f"  Reason: {decision['reason']}")

        # Create chunks
        chunks = create_audio_chunks(
            audio_path=args.audio_path,
            target_duration_sec=decision["target_duration_sec"],
            overlap_sec=decision["overlap_sec"],
            workspace=args.workspace,
            total_duration=media_info.get("duration_sec", args.total_duration),
            chunking_enabled=decision["enabled"],
        )

        # Validate
        duration = media_info.get("duration_sec", args.total_duration)
        if duration:
            warnings = validate_coverage(chunks, duration)
            for w in warnings:
                print(f"  WARNING: {w}", file=sys.stderr)

        # Output
        print(f"\nChunks created: {len(chunks)}")
        for c in chunks:
            status_icon = "✓" if c["status"] == "ready" else "✗"
            print(f"  {status_icon} {c['chunk_id']}: {c['start_sec']:.1f}s – {c['end_sec']:.1f}s ({c['duration_sec']:.1f}s)")

            # Structured output
        output = {
            "decision": decision,
            "chunks": chunks,
            "total_chunks": len(chunks),
            "warnings": validate_coverage(chunks, duration) if duration else [],
        }
        print(f"\nJSON_OUTPUT:{json.dumps(output, default=str)}")

    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
