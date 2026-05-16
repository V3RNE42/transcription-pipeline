#!/usr/bin/env python3
"""
run.py — YouTube transcription pipeline, single process, no subagents.

Usage:
    python3 run.py <URL>

Output:
    /tmp/pipeline_sequential/fetch.json
    /tmp/pipeline_sequential/cleanup.json
    /tmp/pipeline_sequential/qc.json
"""

import json, re, sys, time, textwrap
from pathlib import Path
from typing import Any

WORKSPACE = Path("/tmp/pipeline_sequential")


def extract_video_id(url_or_id: str) -> str:
    url_or_id = url_or_id.strip()
    for p in [r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})',
              r'^([a-zA-Z0-9_-]{11})$']:
        m = re.search(p, url_or_id)
        if m: return m.group(1)
    return url_or_id


def fmt_duration(sec: float) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def phase_fetch(video_id: str) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    try:
        result = api.fetch(video_id, languages=("en",))
    except Exception:
        available = list(api.list(video_id))
        langs = [t.language_code for t in available if t.is_generated][:3] or \
                [t.language_code for t in available[:3]] or ["en"]
        result = api.fetch(video_id, languages=langs)
    return [{"text": seg.text, "start": seg.start, "duration": seg.duration}
            for seg in result]


def phase_qc(segments: list[dict]) -> dict:
    if not segments:
        return {"overall_pass": False, "error": "0 segments"}
    fs = segments[0]["start"]
    le = segments[-1]["start"] + segments[-1].get("duration", 2)
    cov = (le / (le + fs)) * 100
    mono = all(segments[i]["start"] <= segments[i+1]["start"]
               for i in range(len(segments)-1))
    gaps = [(segments[i]["start"] + segments[i].get("duration", 2),
             segments[i+1]["start"])
            for i in range(len(segments)-1)
            if segments[i+1]["start"] - (segments[i]["start"] + segments[i].get("duration", 2)) > 3]
    return {"coverage_pct": round(cov, 2), "coverage_pass": cov >= 98,
            "monotonic_pass": mono, "integrity_pass": len(segments) > 0,
            "real_gaps": len(gaps), "overall_pass": cov >= 98 and mono and len(segments) > 0}


def phase_cleanup(segments: list[dict], block_size: float = 30.0) -> tuple[list[dict], str]:
    blocks = []
    cur, bs = [], 0
    for seg in segments:
        if cur and seg["start"] >= bs + block_size:
            text = re.sub(r"\s+", " ", " ".join(s["text"].strip() for s in cur)).strip()
            blocks.append((bs, text))
            bs = int(seg["start"] // block_size) * block_size
            cur = [seg]
        else:
            if not cur: bs = int(seg["start"] // block_size) * block_size
            cur.append(seg)
    if cur:
        text = re.sub(r"\s+", " ", " ".join(s["text"].strip() for s in cur)).strip()
        blocks.append((bs, text))

    md, bl = [], []
    for s, t in blocks:
        mm, ss = divmod(int(s), 60)
        md.append(f"### [{mm:02d}:{ss:02d}]")
        md.append(textwrap.fill(t, width=80))
        md.append("")
        bl.append({"start_sec": s, "text": t})
    return bl, "\n".join(md)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run.py <URL>"); sys.exit(1)

    url = sys.argv[1]
    vid = extract_video_id(url)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    segs = phase_fetch(vid)
    for s in segs: s["end"] = s["start"] + s["duration"]

    raw = {"video_id": vid, "segment_count": len(segs),
           "duration_sec": segs[-1]["end"] if segs else 0,
           "duration_str": fmt_duration(segs[-1]["end"]) if segs else "0:00",
           "raw_segments": segs}
    (WORKSPACE / "fetch.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False))

    qc = phase_qc(segs)
    (WORKSPACE / "qc.json").write_text(json.dumps(qc, indent=2))

    blocks, md = phase_cleanup(segs)
    clean = {"total_raw_segments": len(segs), "total_blocks": len(blocks),
             "duration_sec": segs[-1]["end"] if segs else 0, "blocks": blocks,
             "cleaned_markdown": md}
    (WORKSPACE / "cleanup.json").write_text(json.dumps(clean, indent=2, ensure_ascii=False))
    (WORKSPACE / "cleaned_transcript.md").write_text(md)

    print(json.dumps({"video_id": vid, "segments": len(segs), "blocks": len(blocks),
                       "duration": fmt_duration(segs[-1]["end"] if segs else 0),
                       "qc_pass": qc["overall_pass"],
                       "coverage": qc["coverage_pct"]}))


if __name__ == "__main__":
    main()
