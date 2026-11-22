#!/usr/bin/env python3
"""Probe local media using ffprobe; JSON to stdout, errors to stderr."""
import argparse
import json
import math
import pathlib
import shutil
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Read video/audio stream metadata using ffprobe.")
    parser.add_argument("media", type=pathlib.Path, help="Path to an existing local media file")
    args = parser.parse_args()
    path = args.media.expanduser().resolve()
    if not path.is_file():
        parser.error(f"Input does not exist: {path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        parser.error("ffprobe is not on PATH. Install FFmpeg/FFprobe first.")
    cmd = [ffprobe, "-v", "error", "-show_entries",
           "format=duration,format_name,size:stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_aspect_ratio,channels:stream_side_data=rotation",
           "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        raw = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"ffprobe failed: {exc}", file=sys.stderr)
        return 1
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        print("Input contains no video stream", file=sys.stderr)
        return 1
    try:
        duration = float(raw.get("format", {}).get("duration", 0))
    except (ValueError, TypeError):
        duration = 0
    if not math.isfinite(duration) or duration <= 0:
        print("Could not obtain a positive finite media duration", file=sys.stderr)
        return 1
    print(json.dumps({
        "path": str(path),
        "duration_seconds": duration,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "sample_aspect_ratio": video.get("sample_aspect_ratio"),
        "avg_frame_rate": video.get("avg_frame_rate"),
        "rotation": [x.get("rotation") for x in video.get("side_data_list", []) if "rotation" in x],
        "audio_streams": sum(s.get("codec_type") == "audio" for s in streams),
        "subtitle_streams": sum(s.get("codec_type") == "subtitle" for s in streams),
        "format": raw.get("format", {}).get("format_name"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
