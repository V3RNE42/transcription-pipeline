#!/usr/bin/env python3
"""
prepare_audio.py — Phase 3: Audio Preparation

Extracts and normalizes audio from a media file to a consistent format
(16-bit PCM, 16 kHz mono WAV). Uses ffmpeg with a pure-Python fallback
for simple WAV-to-WAV conversions.

Usage:
    python prepare_audio.py <source_path> --workspace <dir> [--sample-rate 16000] [--codec pcm_s16le]
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional


def _ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    Path(path).mkdir(parents=True, exist_ok=True)


def _run_ffmpeg(
    source_path: str,
    output_path: str,
    sample_rate: int,
    codec: str,
) -> bool:
    """Extract audio using ffmpeg.

    Returns True on success, False on failure.
    """
    cmd = [
        "ffmpeg",
        "-y",                       # Overwrite output
        "-i", source_path,          # Input file
        "-vn",                      # No video
        "-ar", str(sample_rate),    # Sample rate
        "-ac", "1",                 # Mono
        "-f", codec,                # Codec format
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _convert_wav_python(
    source_path: str,
    output_path: str,
    sample_rate: int,
) -> bool:
    """Pure-Python WAV conversion using the wave module.

    Only works if source is a valid PCM WAV file. Performs:
    - Sample rate conversion via simple linear interpolation
    - Channel downmix (stereo → mono)
    - 16-bit quantization

    This is a best-effort fallback — quality is not comparable to ffmpeg.
    """
    try:
        with wave.open(source_path, "rb") as src:
            src_params = src.getparams()
            src_frames = src.readframes(src_params.nframes)

        # Decode source frames
        src_sample_width = src_params.sampwidth
        src_channels = src_params.nchannels
        src_rate = src_params.framerate
        src_nframes = src_params.nframes

        # Determine source sample format
        src_dtype = (
            f"<{src_channels * src_nframes}h"
            if src_sample_width == 2
            else f"<{src_channels * src_nframes}i"  # 24-bit or 32-bit
        )

        try:
            if src_sample_width == 2:
                samples = list(struct.iter_unpack("<h", src_frames))
                samples = [s[0] for s in samples]
            elif src_sample_width == 4:
                # Try 32-bit int first
                try:
                    samples = list(struct.iter_unpack("<i", src_frames))
                    samples = [s[0] for s in samples]
                except struct.error:
                    return False
            else:
                return False  # Unsupported sample width
        except (struct.error, TypeError):
            return False

        # Reshape into channels
        channel_samples = [
            samples[ch::src_channels]
            for ch in range(src_channels)
        ]

        # Downmix to mono (average channels)
        if src_channels > 1:
            mono = [
                sum(ch[frame] for ch in channel_samples) // src_channels
                for frame in range(len(channel_samples[0]))
            ]
        else:
            mono = channel_samples[0]

        # Simple linear interpolation for sample rate conversion
        if src_rate != sample_rate:
            ratio = sample_rate / src_rate
            new_length = int(len(mono) * ratio)
            resampled = []
            for i in range(new_length):
                src_pos = i / ratio
                src_idx = int(src_pos)
                frac = src_pos - src_idx
                if src_idx + 1 < len(mono):
                    sample = int(mono[src_idx] * (1 - frac) + mono[src_idx + 1] * frac)
                else:
                    sample = mono[src_idx]
                resampled.append(max(-32768, min(32767, sample)))
            mono = resampled

        # Clamp to 16-bit range
        mono = [max(-32768, min(32767, s)) for s in mono]

        # Write output WAV
        with wave.open(output_path, "wb") as dst:
            dst.setnchannels(1)
            dst.setsampwidth(2)  # 16-bit
            dst.setframerate(sample_rate)
            frames = struct.pack(f"<{len(mono)}h", *mono)
            dst.writeframes(frames)

        return True

    except (wave.Error, OSError, struct.error, IOError):
        return False


def prepare_audio(
    source_path: str,
    workspace: str = ".",
    sample_rate: int = 16000,
    codec: str = "pcm_s16le",
) -> str:
    """Extract and normalize audio from a media file.

    Produces a 16-bit PCM mono WAV file optimized for speech-to-text engines.

    Args:
        source_path: Path to the source media file.
        workspace: Working directory for pipeline output.
        sample_rate: Target sample rate in Hz (default: 16000 = Whisper-optimal).
        codec: FFmpeg codec format (default: 'pcm_s16le').

    Returns:
        Absolute path to the prepared WAV file.

    Raises:
        FileNotFoundError: If source_path does not exist.
        ValueError: If the source file is empty or has no readable audio.
        RuntimeError: If audio extraction fails by all available methods.
    """
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    if source.stat().st_size == 0:
        raise ValueError(f"Source file is empty (0 bytes): {source_path}")

    # Ensure workspace/audio/ directory
    audio_dir = Path(workspace) / "audio"
    _ensure_dir(str(audio_dir))

    output_path = str(audio_dir / "prepared_audio.wav")

    # Idempotency check: if output exists and matches input, skip
    if Path(output_path).exists():
        try:
            src_mtime = source.stat().st_mtime
            out_mtime = Path(output_path).stat().st_mtime
            if out_mtime >= src_mtime:
                return output_path
        except OSError:
            pass

    ext = source.suffix.lower()

    # Fast path: source is already a 16kHz mono 16-bit WAV — just copy
    if ext == ".wav":
        is_compatible = _check_wav_compatibility(str(source), sample_rate)
        if is_compatible:
            if str(source.resolve()) != output_path:
                shutil.copy2(str(source), output_path)
            return output_path

    # Primary method: ffmpeg
    ffmpeg_ok = _run_ffmpeg(str(source), output_path, sample_rate, codec)
    if ffmpeg_ok:
        if Path(output_path).exists() and Path(output_path).stat().st_size > 44:
            return output_path

    # Fallback: pure Python WAV conversion
    if ext == ".wav":
        py_ok = _convert_wav_python(str(source), output_path, sample_rate)
        if py_ok:
            if Path(output_path).exists() and Path(output_path).stat().st_size > 44:
                return output_path

    # If we get here, all methods failed
    raise RuntimeError(
        f"Failed to extract audio from '{source_path}'. "
        f"ffmpeg attempted first; Python fallback attempted for WAV inputs. "
        f"Ensure ffmpeg is installed or source is a valid WAV file."
    )


def _check_wav_compatibility(wav_path: str, target_rate: int) -> bool:
    """Check if a WAV file is already in the target format (mono, 16-bit, target rate)."""
    try:
        with wave.open(wav_path, "rb") as wf:
            params = wf.getparams()
            return (
                params.nchannels == 1
                and params.sampwidth == 2  # 16-bit
                and params.framerate == target_rate
                and params.nframes > 0
            )
    except (wave.Error, OSError):
        return False


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Extract and normalize audio from media files"
    )
    parser.add_argument("source", help="Path to the source media file")
    parser.add_argument(
        "--workspace", default=".",
        help="Working directory (default: current directory)"
    )
    parser.add_argument(
        "--sample-rate", type=int, default=16000,
        help="Target sample rate in Hz (default: 16000)"
    )
    parser.add_argument(
        "--codec", default="pcm_s16le",
        help="FFmpeg audio codec format (default: pcm_s16le)"
    )
    args = parser.parse_args()

    try:
        output_path = prepare_audio(
            source_path=args.source,
            workspace=args.workspace,
            sample_rate=args.sample_rate,
            codec=args.codec,
        )
        print(output_path)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
