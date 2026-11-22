# FFmpeg recipes, safety, and troubleshooting

## Prerequisites

Install trusted FFmpeg/FFprobe builds and ensure both are on PATH (`ffmpeg -version`, `ffprobe -version`). The optional `ass` filter requires an FFmpeg build with libass; test with `ffmpeg -filters` before claiming subtitle burn-in works. Use `python --version` and the project's chosen environment.

## Timestamp and container rules

- `start`/`end` refer to the original media timeline, in seconds. For a 120–165 second clip, render 45 seconds and rebase caption timestamps by subtracting 120.
- H.264 GOP/keyframes make `-c copy` extraction imprecise for non-keyframe boundaries. Re-encode for MVP clips.
- Video may use VFR, weird sample-aspect-ratios, display rotation, missing audio, or non-zero start PTS. Probe metadata first; verify the actual output, not assumptions.
- Source timestamps must be finite, nonnegative and ordered. Respect the probed source duration.

## Vertical framing

Preserve all original content (useful for gameplay):

```text
scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1
```

Center crop to fill (may lose HUD, gameplay, or facecam):

```text
scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,crop=1080:1920,setsar=1
```

True subject-aware reframing requires a detection/tracking stage and a crop trajectory with smoothing. Do not call center crop face tracking.

## Recommended output defaults

- Codec: `libx264`, `-crf 20`, `-preset medium` (adjust for hardware/quality).
- Pixel format: `yuv420p`; audio: AAC at 160 kbps if present.
- Output: MP4 with `-movflags +faststart`.
- Optional 30 fps normalization is a product decision; do not silently introduce frame duplication or jerky motion.
- Hardware encoders are optional: test compatibility and compare output quality before switching.

## Windows subtitle-filter quoting

FFmpeg's `ass` filter uses its own quoting rules; raw `C:\...` paths are not safe to paste into a filter string. The bundled renderer copies a subtitle file to a temporary working directory with a simple filename and runs FFmpeg from there, avoiding Windows drive-letter and whitespace parsing hazards.

## Security, failure handling, and observability

- Execute FFmpeg with `subprocess.run([exe, ...], shell=False)`, not raw shell interpolation.
- Reject non-file inputs, missing binaries, invalid timestamps, and existing output files unless `--overwrite` is explicit.
- Use unique temporary directories and delete them after use.
- Do not execute external media URLs automatically or trust metadata paths; obtaining an authorized input is a separate ingestion concern.
- Keep diagnostic output and report command failure. Never silently fall back to an uncaptioned file if the caption filter fails.
- Set upload size/duration quotas before exposing the service publicly; use isolated workers for untrusted uploads. Add malware scanning and decompression/resource limits at deployment time.

## Verification checklist

1. `ffprobe` confirms source video and duration.
2. Exported clip exists, is nonzero, has a video stream and expected dimensions.
3. Exported duration approximately matches `end - start` (allow for encoder/container rounding).
4. If source has sound, the export has audio.
5. If captions were requested, inspect rendered frames and check timing at multiple points.
6. Check landscape gameplay HUD is still visible when padded.
