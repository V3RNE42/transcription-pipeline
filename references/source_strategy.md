# Source Ingestion Strategy

Which pipeline to use depends on the *source type*, not just the file extension.

| Source | Tool | Why |
|--------|------|-----|
| **YouTube URL** (`youtube.com`, `youtu.be`) | `transcription-pipeline` skill (YouTube Integration section) | Skill now includes both YouTube API/yt-dlp SRT path AND full audio pipeline as fallback. Prefer yt-dlp SRT as primary, then youtube-transcript-api, then full audio pipeline. |
| **Local media file** (mp4, wav, mp3, etc.) | `transcription-pipeline` skill | Full 11-phase pipeline: inspect, prepare, chunk, transcribe with faster-whisper, reconstruct, render. |
| **Downloadable URL** (direct link to mp4/wav, not YouTube) | `transcription-pipeline` skill | The pipeline downloads the file in Phase 1 (Ingestion), then runs the standard flow. |
| **Stream URL** (HLS, RTMP, live) | Neither | Out of scope. Tell the user these require a recorded copy first. |

## YouTube Detection Heuristic

When a user shares a URL, check if it's a YouTube link BEFORE loading the transcription pipeline:

```
youtube.com/watch?v=...      → youtube-content
youtu.be/...                 → youtube-content
youtube.com/shorts/...       → youtube-content
youtube.com/embed/...        → youtube-content
youtube.com/live/...         → youtube-content
anything else with a media extension → transcription-pipeline
```

## Why Not to Use transcription-pipeline for YouTube

- **Quality**: YouTube captions are human-generated or high-quality ASR. Whisper on a re-encoded download will have more errors.
- **Speed**: YouTube API returns transcript in <1s. Downloading + re-encoding + whisper transcription takes minutes.
- **Reliability**: YouTube rate-limits downloads; the API is more stable.

## Why Not to Use youtube-content for Local Files

- The `youtube-transcript-api` package only works with YouTube. It cannot process local audio/video files.
- `youtube-content` has no audio extraction or chunking logic.
