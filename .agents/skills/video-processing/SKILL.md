---
name: video-processing
description: Process long-form gaming streams, podcasts, and livestreams into accurate 9:16 short clips using FFprobe and FFmpeg. Use for upload ingestion, source probing, timestamp-accurate extraction, vertical cropping or padding, encoding, audio handling, subtitle burn-in, metadata verification, and debugging broken exports. Do not use for selecting interesting moments or writing highlight scores.
---

# Video processing for AI clipping

## Scope and entry criteria

Use when a task involves decoding, probing, extracting, reframing, encoding, rendering, or validating **actual video/audio media**. This skill does not choose highlights; get source-relative `start` and `end` seconds from the highlight-detection stage. Captions are optional inputs produced by caption-generation.

Before implementing, inspect the repository's existing media pipeline and follow its conventions. Do not replace working components without a reason. Never claim a playable clip was generated unless FFmpeg succeeded and the exported file was probed.

## Canonical contract

- A source has a stable `video_id`, absolute filesystem path or authorized storage reference, and validated duration in seconds.
- A candidate clip uses **source-relative** `start` and `end` seconds, not wall-clock timestamps, frame indices, or clip-relative times.
- Reject non-finite timestamps, `start < 0`, `end <= start`, and `end` beyond the source duration except a documented tiny rounding tolerance.
- Video output is H.264/AAC MP4, `yuv420p`, square pixels, 1080×1920 where feasible, and fast-start enabled. Keep source audio if it exists; silent videos are valid.
- For gameplay, begin with `pad` (no visual information discarded); choose `crop` only when the actual focal region remains visible. Center-cropping is a fallback, **not** subject tracking.
- Use absolute paths for source and output. Do not interpolate filenames into shell command strings; pass argument arrays to subprocess. Avoid overwriting files without an explicit request.

## Procedure

1. Probe the source with `scripts/probe_media.py`. Verify video stream, duration, codec, frame size, rotation, audio existence, and whether subtitles are already present.
2. Validate proposed source timestamps against probed duration. Prefer clips around 15–60 seconds for the first MVP; let the content justify exceptions.
3. Choose framing based on content: `pad` for gaming to preserve the full frame; `crop` only for safe center-weighted material. Facecam/gameplay layouts require a later content-aware layout engine, not blind center crop.
4. For subtitles, use a clip-relative ASS file produced by caption-generation; never apply source-absolute timestamps to a shortened clip.
5. Render with `scripts/render_clip.py`. The script uses re-encoding for accurate boundaries, a constrained FFmpeg invocation, and an optional ASS overlay.
6. Probe the export again. Verify approximately expected duration, width/height, presence of video, and whether audio survived if source had it. Watch the first, middle, and final 2 seconds for sync, black frames, clipping, and caption placement.
7. Report the exact commands, output paths, dimensions, and verified limitations. On failure, retain useful logs and do not mark the job successful.

## Commands (from the repository root)

```powershell
python .github/skills/video-processing/scripts/probe_media.py "C:\\videos\\stream.mp4"
python .github/skills/video-processing/scripts/render_clip.py --input "C:\\videos\\stream.mp4" --start 120 --end 165 --output "C:\\clips\\clip-001.mp4" --mode pad
# If the caption-generation stage has produced CLIP-RELATIVE ASS subtitles:
python .github/skills/video-processing/scripts/render_clip.py --input "C:\\videos\\stream.mp4" --start 120 --end 165 --output "C:\\clips\\clip-001-captioned.mp4" --mode pad --ass "C:\\clips\\clip-001.ass"
```

Read [FFmpeg guide and failure cases](references/FFMPEG_GUIDE.md) for encoding tradeoffs, Windows filter pitfalls, VFR, GPU and quality testing.

## Acceptance criteria

- Actual exported file opens in a media player; output `ffprobe` reports video width 1080, height 1920, duration within a documented tolerance of requested duration.
- No black bars are incorrectly described as an intelligent crop. A padded gameplay export is acceptable in the initial MVP.
- Audio and captions, when applicable, are synchronized on the exported file.
- No change to existing uploads or outputs without user approval.
