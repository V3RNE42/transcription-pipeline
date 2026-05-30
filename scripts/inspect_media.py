#!/usr/bin/env python3
"""
inspect_media.py — Phase 2: Technical Media Inspection

Extracts technical metadata from audio/video files using ffprobe (preferred)
or pure-Python fallbacks. Returns a structured dict with duration, codec info,
channel counts, bitrate, and detected risks.

Usage:
    python inspect_media.py <filepath>
    # Returns JSON to stdout
"""

import json
import mimetypes
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run_ffprobe(filepath: str) -> dict[str, Any] | None:
    """Attempt to extract media info using ffprobe.

    Returns parsed JSON dict on success, None if ffprobe is unavailable
    or returns a non-zero exit code.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _parse_wav_header(filepath: str) -> dict[str, Any]:
    """Parse a WAV file header using pure Python (struct).

    Returns sample_rate, num_channels, bit_depth, duration if possible.
    """
    info: dict[str, Any] = {}
    try:
        with open(filepath, "rb") as f:
            riff, file_size = struct.unpack("<4sI", f.read(8))
            if riff != b"RIFF":
                return {}
            f.read(4)  # WAVE
            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack("<I", f.read(4))[0]
                if chunk_id == b"fmt ":
                    fmt_data = f.read(16)
                    if len(fmt_data) >= 16:
                        (
                            audio_format, num_channels, sample_rate,
                            byte_rate, block_align, bits_per_sample
                        ) = struct.unpack("<HHIIHH", fmt_data)
                        info["audio_format"] = audio_format
                        info["channels"] = num_channels
                        info["sample_rate"] = sample_rate
                        info["bit_depth"] = bits_per_sample
                        info["byte_rate"] = byte_rate
                    # Skip remaining fmt chunk
                    if chunk_size > 16:
                        f.read(chunk_size - 16)
                elif chunk_id == b"data":
                    data_bytes = chunk_size
                    sample_rate = info.get("sample_rate", 0)
                    channels = info.get("channels", 0)
                    if sample_rate and channels:
                        info["duration_sec"] = data_bytes / (
                            sample_rate * channels * (info.get("bit_depth", 16) / 8)
                        )
                    info["data_size"] = data_bytes
                    break
                else:
                    f.read(chunk_size)
    except (IOError, struct.error):
        pass
    return info


def _detect_container(filepath: str) -> str:
    """Detect file container format from extension."""
    ext = Path(filepath).suffix.lower()
    container_map = {
        ".mp4": "mp4",
        ".mkv": "matroska",
        ".webm": "webm",
        ".avi": "avi",
        ".mov": "quicktime",
        ".wmv": "asf",
        ".flv": "flv",
        ".mp3": "mp3",
        ".wav": "wav",
        ".m4a": "mp4",
        ".aac": "aac",
        ".ogg": "ogg",
        ".flac": "flac",
        ".opus": "ogg",
        ".wma": "asf",
        ".ts": "mpegts",
        ".mts": "mpegts",
        ".m2ts": "mpegts",
        ".3gp": "3gp",
    }
    return container_map.get(ext, f"unknown ({ext})")


def inspect_media(filepath: str) -> dict[str, Any]:
    """Inspect a media file and return structured technical metadata.

    Args:
        filepath: Path to the media file (audio or video).

    Returns:
        A dictionary containing:
            - duration_sec: Total duration in seconds (float).
            - size_bytes: File size in bytes (int).
            - bitrate_kbps: Overall bitrate in kbps (float or None).
            - has_audio_track: Whether an audio track was found (bool).
            - audio_channels: Number of audio channels (int or None).
            - audio_sample_rate: Audio sample rate in Hz (int or None).
            - video_codec: Video codec name (str or None).
            - audio_codec: Audio codec name (str or None).
            - container: Detected container format (str).
            - risks: List of risk strings detected (list[str]).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is empty (0 bytes).
    """
    path = Path(filepath)

    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {filepath}")

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise ValueError(f"File is empty (0 bytes): {filepath}")

    risks: list[str] = []
    result: dict[str, Any] = {
        "duration_sec": None,
        "size_bytes": size_bytes,
        "bitrate_kbps": None,
        "has_audio_track": False,
        "audio_channels": None,
        "audio_sample_rate": None,
        "video_codec": None,
        "audio_codec": None,
        "container": _detect_container(filepath),
        "risks": risks,
    }

    # Try ffprobe first
    ffprobe_data = _run_ffprobe(filepath)
    if ffprobe_data:
        _parse_ffprobe_output(ffprobe_data, result)
    else:
        # Fallback: pure Python parsing
        _fallback_parse(filepath, result)

    # Detect risks
    if result["duration_sec"] is None:
        risks.append("Could not determine duration")
    elif result["duration_sec"] == 0:
        risks.append("Zero-length media — file may be corrupt")
    elif result["duration_sec"] > 7200:
        risks.append(f"Long-form content ({result['duration_sec'] / 60:.0f} min) — chunking recommended")

    if result["audio_sample_rate"] is not None and result["audio_sample_rate"] < 8000:
        risks.append(f"Low sample rate ({result['audio_sample_rate']} Hz) — may degrade transcription quality")

    if result["audio_channels"] is not None and result["audio_channels"] > 2:
        risks.append(f"Multi-channel audio ({result['audio_channels']} channels) — will be downmixed to mono")

    if result["bitrate_kbps"] is not None and result["bitrate_kbps"] < 32:
        risks.append(f"Very low bitrate ({result['bitrate_kbps']} kbps) — audio quality may be poor")

    # Check for file extension / container mismatch
    ext = path.suffix.lower()
    container_ext_map = {".mp4": "mp4", ".mkv": "matroska", ".webm": "webm",
                         ".mp3": "mp3", ".wav": "wav", ".flac": "flac",
                         ".ogg": "ogg", ".m4a": "mp4"}
    expected = container_ext_map.get(ext)
    if expected and result["container"] != expected and not result["container"].startswith("unknown"):
        risks.append(f"Container mismatch: extension '{ext}' suggests '{expected}' but detected '{result['container']}'")

    return result


def _parse_ffprobe_output(data: dict[str, Any], result: dict[str, Any]) -> None:
    """Extract metadata from ffprobe JSON output into the result dict."""
    # Format-level info
    fmt = data.get("format", {})
    if fmt.get("duration"):
        try:
            result["duration_sec"] = float(fmt["duration"])
        except (ValueError, TypeError):
            pass
    if fmt.get("bit_rate"):
        try:
            result["bitrate_kbps"] = round(int(fmt["bit_rate"]) / 1000, 1)
        except (ValueError, TypeError):
            pass
    if fmt.get("format_name"):
        result["container"] = fmt["format_name"]

    # Stream-level info
    audio_streams = []
    video_streams = []
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            audio_streams.append(stream)
        elif codec_type == "video":
            video_streams.append(stream)

    if audio_streams:
        primary = audio_streams[0]
        result["has_audio_track"] = True
        result["audio_codec"] = primary.get("codec_name")
        result["audio_channels"] = primary.get("channels")
        result["audio_sample_rate"] = (
            int(primary["sample_rate"]) if primary.get("sample_rate") else None
        )
        if len(audio_streams) > 1:
            result["risks"].append(
                f"Multiple audio tracks ({len(audio_streams)}) — using first track"
            )

    if video_streams:
        primary = video_streams[0]
        result["video_codec"] = primary.get("codec_name")


def _fallback_parse(filepath: str, result: dict[str, Any]) -> None:
    """Fallback: parse metadata using Python standard library."""
    ext = Path(filepath).suffix.lower()

    # Try WAV header parsing
    if ext == ".wav":
        wav_info = _parse_wav_header(filepath)
        if wav_info:
            result["has_audio_track"] = True
            result["audio_sample_rate"] = wav_info.get("sample_rate")
            result["audio_channels"] = wav_info.get("channels")
            result["audio_codec"] = "pcm"
            result["duration_sec"] = wav_info.get("duration_sec")
            if wav_info.get("byte_rate"):
                result["bitrate_kbps"] = round(
                    (wav_info["byte_rate"] * 8) / 1000, 1
                )

    # Estimate duration from file size for common CBR audio formats
    if result["duration_sec"] is None:
        estimated_duration = _estimate_duration_from_size(filepath, ext)
        if estimated_duration:
            result["duration_sec"] = estimated_duration
            result["risks"].append(
                f"Duration estimated from file size ({estimated_duration:.1f}s)"
            )

    # Treat as audio if extension suggests it
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".wma"}
    if ext in audio_exts:
        result["has_audio_track"] = True
        result["audio_codec"] = ext.lstrip(".")

    # Mimetype-based fallback
    mime_type, _ = mimetypes.guess_type(filepath)
    if mime_type:
        if mime_type.startswith("video/"):
            result["video_codec"] = "unknown"
            result["risks"].append("Video detected but codec unknown (ffprobe unavailable)")
        elif mime_type.startswith("audio/"):
            result["has_audio_track"] = True
            if not result["audio_codec"]:
                result["audio_codec"] = mime_type.split("/")[-1]

    if not result["has_audio_track"]:
        result["risks"].append("No audio track detected — transcription may produce empty output")


def _estimate_duration_from_size(filepath: str, ext: str) -> float | None:
    """Rough duration estimate for common CBR audio formats based on file size."""
    try:
        size = os.path.getsize(filepath)
        # Rough bitrate assumptions (kbps)
        estimates = {
            ".mp3": 128,    # Typical CBR MP3
            ".ogg": 128,    # Typical Ogg Vorbis
            ".aac": 128,    # Typical AAC
            ".wma": 128,    # Typical WMA
        }
        bitrate_kbps = estimates.get(ext)
        if bitrate_kbps and bitrate_kbps > 0:
            return (size * 8) / (bitrate_kbps * 1000)
    except OSError:
        pass
    return None


def main() -> None:
    """CLI entry point: inspect a media file and print JSON to stdout."""
    if len(sys.argv) < 2:
        print("Usage: python inspect_media.py <media_file>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    try:
        info = inspect_media(filepath)
        print(json.dumps(info, indent=2, default=str))
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"error": str(e), "risks": [str(e)]}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Unexpected error: {e}", "risks": [str(e)]}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
