# YouTube Transcription Pipeline — Agent Workflow

When the user shares a YouTube URL, run the unified pipeline script, then generate an abstractive summary, save to vault, interlink, and deliver only the summary.

## Script

```bash
python3 scripts/run.py "https://youtube.com/watch?v=VIDEO_ID"
```

Outputs JSON with: title, video_id, slug, duration, lang, method, qc, blocks[], raw_segments[], clean_transcript.

## Phases

1. **Fetch** — Script tries E1 (YouTube API) → E3 (whisper chunked 10 min) → E2 (whisper whole file). Automatic cascade.
2. **QC** — Coverage ≥90%, chronological order, integrity. Run inline.
3. **Cleanup** — Groups into ~30s blocks with `### [MM:SS]` markers. Handled by script.
4. **Summary** — Generate abstractive summary (LLM). Tiers: ≤20 min=300 chars, 20–40=450, 40–60=800, >60=1000. Plain text, no formatting.
5. **Vault** — `$VAULT/Transcripciones/YouTube/<video_id>_<slug>.md`. Includes full transcript, not just summary.
6. **Delivery** — ONE message with ONLY the summary. No metadata, no QC, no progress.
7. **Interlink** — Append `[[wikilinks]]` to related vault notes. Append-only, never read/write round-trip.

## Rules

- **Code-first**: All mechanical work via script. LLM only for summaries.
- **Zero verbosity**: User sees only the summary. No tool output, no progress, no paths.
- **One-call**: One `execute_code` call for the script, not fragmented.
- **Audio cleanup**: Script deletes downloaded audio after transcription.

## Pitfalls

See README.md for detailed pitfalls (yt-dlp template, PATH, Whisper Segment API, etc.).

## Error Handling

- Transcript disabled → cascade handles it automatically. No user intervention.
- Private/unavailable video → relay error, ask user to verify URL.
