#!/usr/bin/env python3
"""
validate_summary.py — Enforce tiered summary length with sentence-boundary truncation.

Usage (pipe mode):
    echo "summary text here" | python3 validate_summary.py --duration 593

Usage (file mode):
    python3 validate_summary.py --text "summary text" --duration 593

Exits with code 0 on success, 1 if truncated, 2 if error.
Prints JSON to stdout with: validated_text, original_length, validated_length,
                             tier_limit, tier_label, truncated, exit_code.
"""

import argparse
import json
import re
import sys

# Tiers: (max_seconds, label, char_limit)
TIERS = [
    (1200, "≤20 min", 300),    # 20 min
    (2400, "20-40 min", 450),  # 40 min
    (3600, "40-60 min", 800),  # 60 min
    (float("inf"), ">60 min", 1000),
]


def get_tier(duration_sec: float) -> tuple[str, int]:
    """Return (tier_label, char_limit) for the given duration in seconds."""
    for max_sec, label, limit in TIERS:
        if duration_sec <= max_sec:
            return label, limit
    return ">60 min", 1000  # fallback


def truncate_at_sentence(text: str, limit: int) -> str:
    """Truncate text at the last sentence boundary before `limit` chars."""
    if len(text) <= limit:
        return text

    # Try sentence-ending punctuation within the limit
    truncated = text[:limit]
    # Find last sentence boundary (. ? !) followed by space or end
    for sep in (". ", "? ", "! "):
        pos = truncated.rfind(sep)
        if pos > limit * 0.5:  # Only truncate if we keep at least half
            return truncated[: pos + 1]

    # Try last space as fallback
    last_space = truncated.rfind(" ")
    if last_space > limit * 0.5:
        return truncated[:last_space] + "..."

    # Hard truncate with ellipsis
    return truncated.rstrip() + "..."


def validate_summary(text: str, duration_sec: float) -> dict:
    """
    Validate and optionally truncate a summary to fit the tier limit.

    Returns dict with:
        validated_text: str
        original_length: int
        validated_length: int
        tier_label: str
        tier_limit: int
        truncated: bool
        exit_code: int  (0 = ok, 1 = was truncated)
    """
    tier_label, tier_limit = get_tier(duration_sec)
    original_length = len(text)

    if original_length <= tier_limit:
        return {
            "validated_text": text,
            "original_length": original_length,
            "validated_length": original_length,
            "tier_label": tier_label,
            "tier_limit": tier_limit,
            "truncated": False,
            "exit_code": 0,
        }

    validated = truncate_at_sentence(text, tier_limit)
    validated_length = len(validated)

    return {
        "validated_text": validated,
        "original_length": original_length,
        "validated_length": validated_length,
        "tier_label": tier_label,
        "tier_limit": tier_limit,
        "truncated": True,
        "exit_code": 1,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate summary length against duration-based tier limits."
    )
    parser.add_argument(
        "--duration",
        type=float,
        required=True,
        help="Video duration in seconds (e.g., 593 for 9:53)",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Summary text. If omitted, reads from stdin.",
    )
    args = parser.parse_args()

    if args.text:
        text = args.text
    else:
        text = sys.stdin.read().strip()

    if not text:
        result = {
            "error": "Empty summary text",
            "exit_code": 2,
        }
        print(json.dumps(result))
        sys.exit(2)

    result = validate_summary(text, args.duration)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
