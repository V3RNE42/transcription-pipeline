# Transcription Pipeline

Code-first YouTube & media transcription pipeline. Fetch, QC, cleanup, summarize, and vault-interlink — without subagent overhead.

## Philosophy

**Everything that can be done via code, MUST be done via code.** Only use LLM reasoning for what genuinely needs it (summaries). No subagents, no progress spam, no metadata in delivery — just the result.

## Quick Start

```bash
pip install youtube-transcript-api

# YouTube video → fetch + QC + cleanup
python scripts/run.py "https://youtu.be/VIDEO_ID"
```

The script outputs:
- `/tmp/pipeline_sequential/fetch.json` — raw segments
- `/tmp/pipeline_sequential/qc.json` — quality checks
- `/tmp/pipeline_sequential/cleanup.json` — ~30s blocks
- `/tmp/pipeline_sequential/cleaned_transcript.md` — formatted markdown

## Pipeline

| Phase | What | Who |
|-------|------|-----|
| 1. Fetch | YouTube transcript via API | Code (1.3s) |
| 2. QC | Coverage ≥98%, monotonic, integrity | Code (0.0s) |
| 3. Cleanup | Raw segments → ~30s blocks with `### [MM:SS]` | Code (0.1s) |
| 4. Summary | Unified summary ≤1000 chars | LLM |
| 5. Vault | Save to Obsidian vault | Code |
| 6. Delivery | Only the summary — no metadata, no QC, no timing | LLM |
| 7. Interlink | `[[wikilinks]]` between related vault notes | Code |

## Benchmark

Processing a 23:54 video:

| Approach | Time | Subagents |
|----------|------|-----------|
| Subagent wave pattern (5 delegate_task) | **~6 min** | 5 |
| Code-first sequential | **~4 s** | **0** |

**Sequential is ~90× faster** for procedural tasks. Subagent overhead (context load, LLM per subagent, serial dependencies) dwarfs actual compute.

## Vault Integration

Vault path: `~/vault/Transcripciones/YouTube/<title>.md`

Post-delivery, the pipeline scans all existing vault notes and adds `[[wikilinks]]` between related ones — automatic graph building.

## Delivery Format

Only the summary text. Plain. No bold, no headers, no emoji, no bullets, no metadata, no QC report, no timing, no vault path. Just the content.

```python
assert len(summary) <= 1000  # verified
```

## Requirements

- Python 3.9+
- `youtube-transcript-api`
