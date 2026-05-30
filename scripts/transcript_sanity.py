#!/usr/bin/env python3
"""
transcript_sanity.py — Sanity check for transcript completeness.

Validates that a transcript file has enough content relative to the video
duration. Catches cases where a chapter summary, outline, or placeholder
is mistaken for a full transcript.

Usage:
    python3 transcript_sanity.py <transcript_file.md>

Checks:
  - Extracts duration from YAML frontmatter (duration: Ns or duracion: MM:SS)
  - Counts body words (excluding frontmatter)
  - Compares against age-adjusted minimum WPM thresholds:
      < 10 min:  50 wpm floor (very short videos may be dense)
      10-60 min: 40 wpm floor
      > 60 min:  30 wpm floor (lectures/courses have natural pauses)
  - Also checks chars/line ratio (< 40 chars/line avg -> likely structured/summary)

Exit codes:
  0 - PASS (looks like a real transcript)
  1 - FAIL (proportionally too sparse)
  2 - WARN (below comfortable threshold)
  3 - ERROR (can't read file or parse duration)
"""

import re
import sys
import os


def parse_duration(content: str) -> tuple:
    """
    Parse duration from YAML frontmatter.
    Supports:
      - duration: 27518s
      - duracion: 458:38  (MM:SS)
      - duration: 7:38:18 (HH:MM:SS)
    Returns (duration_seconds, source_field) or (None, error_msg).
    """
    if not content.startswith('---'):
        return None, "No YAML frontmatter found (file must start with '---')"

    parts = content.split('---', 2)
    if len(parts) < 3:
        return None, "Malformed frontmatter (no closing '---')"

    fm = parts[1]

    # Try duration: Ns
    m = re.search(r'^duration:\s*(\d+)s\s*$', fm, re.MULTILINE)
    if m:
        return int(m.group(1)), 'duration: Ns'

    # Try duration: HH:MM:SS or MM:SS
    m = re.search(r'^duration:\s*(\d+):(\d+):(\d+)\s*$', fm, re.MULTILINE)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + s, 'duration: HH:MM:SS'

    m = re.search(r'^duration:\s*(\d+):(\d+)\s*$', fm, re.MULTILINE)
    if m:
        mi, s = int(m.group(1)), int(m.group(2))
        return mi * 60 + s, 'duration: MM:SS'

    # Try legacy Spanish: duracion: MM:SS
    m = re.search(r'^duracion:\s*(\d+):(\d+)\s*$', fm, re.MULTILINE)
    if m:
        mi, s = int(m.group(1)), int(m.group(2))
        return mi * 60 + s, 'duracion: MM:SS'

    return None, "No recognized duration field found (expected 'duration: Ns', 'duration: HH:MM:SS', or legacy 'duracion: MM:SS')"


def get_body(content: str) -> str:
    """Extract body text excluding YAML frontmatter."""
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            return parts[2]
    return content


def count_body_words(content: str) -> int:
    """Count words in the body (excluding frontmatter)."""
    body = get_body(content)
    return len(body.split())


def count_body_lines(content: str) -> int:
    """Count non-empty lines in the body."""
    body = get_body(content).strip()
    if not body:
        return 0
    return len([l for l in body.split('\n') if l.strip()])


def wpm_threshold(duration_min: float) -> tuple:
    """Returns (absolute_min_wpm, warn_wpm, label) based on video duration."""
    if duration_min < 10:
        return 50, 90, "short (< 10 min)"
    elif duration_min < 60:
        return 40, 75, "medium (10-60 min)"
    else:
        return 30, 60, "long (> 60 min)"


def run_sanity(path: str) -> dict:
    """Run all sanity checks on a transcript file. Returns result dict."""
    if not os.path.isfile(path):
        return {'status': 3, 'error_msg': f"File not found: {path}"}

    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Parse duration
    duration_sec, dur_note = parse_duration(content)
    if duration_sec is None:
        return {'status': 3, 'error_msg': f"Could not parse duration: {dur_note}"}

    duration_min = duration_sec / 60

    # Count metrics
    words = count_body_words(content)
    lines = count_body_lines(content)
    body = get_body(content)
    chars = len(body)

    wpm = words / duration_min if duration_min > 0 else 0
    chars_per_line = chars / lines if lines > 0 else 0

    # Thresholds
    min_wpm, warn_wpm, duration_label = wpm_threshold(duration_min)
    expected_min = duration_min * min_wpm
    warn_at = duration_min * warn_wpm

    results = {
        'status': 0,
        'duration_sec': duration_sec,
        'duration_min': round(duration_min, 1),
        'words': words,
        'lines': lines,
        'chars': chars,
        'wpm': round(wpm, 1),
        'chars_per_line': round(chars_per_line, 1),
        'duration_label': duration_label,
        'min_wpm': min_wpm,
        'warn_wpm': warn_wpm,
        'expected_min_words': round(expected_min),
        'warn_words': round(warn_at),
        'msg': '',
    }

    # Check 1: Word count vs duration
    if words < expected_min:
        pct = words / expected_min * 100
        results['status'] = 1
        results['msg'] = (
            f"FAIL: {words:,} words is only {pct:.1f}% of expected "
            f"minimum ({expected_min:,.0f} words @ {min_wpm} wpm). "
            f"This is NOT a full transcript."
        )
    elif words < warn_at:
        pct = words / warn_at * 100
        results['status'] = 2
        results['msg'] = (
            f"WARN: {words:,} words is only {pct:.1f}% of comfortable "
            f"threshold ({warn_at:,.0f} words @ {warn_wpm} wpm). "
            f"Transcript may be sparse."
        )
    else:
        results['msg'] = f"PASS: {words:,} words ({wpm:.0f} wpm) - looks like a real transcript."

    # Check 2: chars per line (structured summaries have short lines)
    results['chars_per_line_note'] = ''
    if chars_per_line < 40 and words > expected_min * 0.3:
        results['chars_per_line_note'] = (
            f"Low chars/line ({chars_per_line:.0f}) - suggests structured/summary format, "
            f"not prose transcript."
        )

    return results


def print_report(results: dict):
    """Pretty-print the sanity check report."""
    if results['status'] == 3:
        print("=" * 60)
        print("TRANSCRIPT SANITY CHECK - ERROR")
        print("=" * 60)
        print(f"  {results.get('error_msg', 'Unknown error')}")
        print(f"  Exit: ERROR (code 3)")
        print("=" * 60)
        return

    print("=" * 60)
    print("TRANSCRIPT SANITY CHECK")
    print("=" * 60)
    print(f"  Duration:       {results['duration_sec']}s ({results['duration_min']:.1f} min)")
    print(f"  Duration type:  {results['duration_label']}")
    print(f"  Words:          {results['words']:,}")
    print(f"  Lines:          {results['lines']:,}")
    print(f"  Chars:          {results['chars']:,}")
    print(f"  WPM:            {results['wpm']}")
    print(f"  Chars/line:     {results['chars_per_line']}")
    print()
    print(f"  Thresholds:     min {results['min_wpm']} wpm | warn {results['warn_wpm']} wpm")
    print(f"  Expected min:   {results['expected_min_words']:,} words")
    print(f"  Warn at:        {results['warn_words']:,} words")
    print()
    print(f"  {results['msg']}")
    if results.get('chars_per_line_note'):
        print(f"  {results['chars_per_line_note']}")
    print()
    status_label = {0: 'PASS', 2: 'WARN', 1: 'FAIL', 3: 'ERROR'}.get(results['status'], '???')
    print(f"  Exit: {status_label} (code {results['status']})")
    print("=" * 60)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 transcript_sanity.py <transcript_file.md>", file=sys.stderr)
        sys.exit(3)

    path = sys.argv[1]
    results = run_sanity(path)
    print_report(results)
    sys.exit(results['status'])
