#!/usr/bin/env python3
"""
validate_summary.py — Validate summary length against duration-based tier limits.

NO truncation. If the summary exceeds the tier limit, the script rejects it
with exit_code=1 and reports by how much. The caller must regenerate a shorter
summary until exit_code=0.

Usage (pipe mode):
    echo "summary text here" | python3 validate_summary.py --duration 593

Usage (direct mode):
    python3 validate_summary.py --text "summary text" --duration 593

Exit codes:
    0 = PASS — summary fits within tier limit
    1 = FAIL — summary is too long (see excess_chars in JSON output)
    2 = ERROR — empty input or invalid arguments

Output JSON:
    {
        "original_text": "...",
        "original_length": 384,
        "tier_label": "≤20 min",
        "tier_limit": 300,
        "excess_chars": 84,
        "fits": false,
        "exit_code": 1
    }
"""

import argparse
import json
import sys

# Tiers: (max_seconds, label, char_limit)
TIERS = [
    (1200, "≤20 min", 300),
    (2400, "20-40 min", 450),
    (3600, "40-60 min", 800),
    (float("inf"), ">60 min", 1000),
]


def get_tier(duration_sec: float) -> tuple[str, int]:
    """Return (tier_label, char_limit) for the given duration in seconds."""
    for max_sec, label, limit in TIERS:
        if duration_sec <= max_sec:
            return label, limit
    return ">60 min", 1000


def validate_summary(text: str, duration_sec: float) -> dict:
    """
    Validate summary length against tier limit.

    NO truncation — if too long, returns reject signal with excess_chars.
    The caller must regenerate a shorter summary and re-validate.
    """
    tier_label, tier_limit = get_tier(duration_sec)
    original_length = len(text)
    excess = original_length - tier_limit

    fits = original_length <= tier_limit

    return {
        "original_text": text,
        "original_length": original_length,
        "tier_label": tier_label,
        "tier_limit": tier_limit,
        "excess_chars": max(0, excess),
        "fits": fits,
        "exit_code": 0 if fits else 1,
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
        text = args.text.strip()
    else:
        text = sys.stdin.read().strip()

    if not text:
        result = {"error": "Empty summary text", "exit_code": 2}
        print(json.dumps(result))
        sys.exit(2)

    result = validate_summary(text, args.duration)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(result["exit_code"])


if __name__ == "__main__":
    main()
