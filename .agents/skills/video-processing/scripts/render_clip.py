#!/usr/bin/env python3
"""Create a vertical MP4 from a source interval, optionally burning ASS captions."""
import argparse
import json
import math
import pathlib
import shutil
import subprocess
import sys
import tempfile

PAD_FILTER = ("scale=1080:1920:force_original_aspect_ratio=decrease:"
              "force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1")
CROP_FILTER = ("scale=1080:1920:force_original_aspect_ratio=increase:"
               "force_divisible_by=2,crop=1080:1920,setsar=1")


def probe_duration(path: pathlib.Path, ffprobe: str) -> float:
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    result = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=120)
    value = float(result.stdout.strip())
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Source duration must be positive and finite")
    return value


def inspect_streams(path: pathlib.Path, ffprobe: str):
    """Return (width, height, audio_present) for a probed local file."""
    cmd = [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height",
           "-of", "json", str(path)]
    result = subprocess.run(cmd, text=True, capture_output=True, check=True, timeout=120)
    streams = json.loads(result.stdout).get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"No video stream found in {path}")
    return video.get("width"), video.get("height"), any(s.get("codec_type") == "audio" for s in streams)


def main() -> int:
    p = argparse.ArgumentParser(description="Render source-relative interval to 1080x1920 MP4 using FFmpeg.")
    p.add_argument("--input", required=True, type=pathlib.Path)
    p.add_argument("--start", required=True, type=float, help="Start in original source seconds")
    p.add_argument("--end", required=True, type=float, help="End in original source seconds")
    p.add_argument("--output", required=True, type=pathlib.Path)
    p.add_argument("--mode", choices=["pad", "crop"], default="pad")
    p.add_argument("--ass", type=pathlib.Path, help="Clip-relative ASS subtitles (requires FFmpeg libass)")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--preset", choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"], default="medium")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    source = args.input.expanduser().resolve()
    dest = args.output.expanduser().resolve()
    subtitle = args.ass.expanduser().resolve() if args.ass else None
    if not source.is_file():
        p.error(f"Source file does not exist: {source}")
    if source == dest:
        p.error("Output must not overwrite the input source")
    if dest.exists() and not args.overwrite:
        p.error(f"Output already exists; add --overwrite to replace it: {dest}")
    if dest.suffix.lower() != ".mp4":
        p.error("Output must end in .mp4")
    if subtitle and (not subtitle.is_file() or subtitle.suffix.lower() != ".ass"):
        p.error("--ass must point to an existing .ass file")
    if not (math.isfinite(args.start) and math.isfinite(args.end)) or args.start < 0 or args.end <= args.start:
        p.error("Require finite 0 <= --start < --end")
    if args.crf < 0 or args.crf > 51:
        p.error("--crf must be between 0 and 51")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        p.error("ffmpeg and ffprobe must be installed and available on PATH")
    try:
        duration = probe_duration(source, ffprobe)
        _, _, source_audio = inspect_streams(source, ffprobe)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        print(f"Unable to inspect source duration: {exc}", file=sys.stderr)
        return 1
    if args.end > duration + 0.05:
        p.error(f"Clip end {args.end} exceeds source duration {duration:.3f}")
    if args.end > duration:
        args.end = duration  # small container metadata rounding tolerance
    dest.parent.mkdir(parents=True, exist_ok=True)
    requested_duration = args.end - args.start
    base_filter = PAD_FILTER if args.mode == "pad" else CROP_FILTER
    # A simple working directory and basename avoid Windows C: and backslash escaping in FFmpeg filters.
    with tempfile.TemporaryDirectory(prefix="clip-ass-") as temp_dir:
        filter_chain = base_filter
        if subtitle:
            shutil.copyfile(subtitle, pathlib.Path(temp_dir) / "captions.ass")
            filter_chain += ",ass=captions.ass"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{args.start:.6f}", "-i", str(source), "-t", f"{requested_duration:.6f}",
               "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", filter_chain,
               "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
               "-movflags", "+faststart", "-y" if args.overwrite else "-n", str(dest)]
        print(f"Rendering {requested_duration:.2f}s from {source.name} ({args.mode})...", file=sys.stderr)
        run = subprocess.run(cmd, cwd=temp_dir if subtitle else None, text=True, capture_output=True)
        if run.returncode != 0:
            print(f"FFmpeg failed ({run.returncode}):\n{run.stderr[-6000:]}", file=sys.stderr)
            return run.returncode if run.returncode > 0 else 1
    # Probe successful output; do not mark success merely because a filename exists.
    if not dest.is_file() or dest.stat().st_size == 0:
        print("FFmpeg returned success but output file is missing or empty", file=sys.stderr)
        return 1
    try:
        out_duration = probe_duration(dest, ffprobe)
        out_width, out_height, output_audio = inspect_streams(dest, ffprobe)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        print(f"Rendered output could not be probed: {exc}", file=sys.stderr)
        return 1
    if (out_width, out_height) != (1080, 1920):
        print(f"Unexpected output dimensions: {out_width}x{out_height} (expected 1080x1920)", file=sys.stderr)
        return 1
    if source_audio and not output_audio:
        print("Output audio stream is missing despite source audio", file=sys.stderr)
        return 1
    if abs(out_duration - requested_duration) > max(0.5, requested_duration * 0.02):
        print(f"Warning: duration mismatch: requested {requested_duration:.3f}s, got {out_duration:.3f}s", file=sys.stderr)
    print(f"Rendered: {dest}\nDuration: {out_duration:.3f}s (requested {requested_duration:.3f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
