#!/usr/bin/env python3
"""Split timestamped transcript into overlapping source-timed windows."""
import argparse
import json
import math
import pathlib
import sys


def checked_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite numeric timestamp")
    return float(value)


def main() -> int:
    p = argparse.ArgumentParser(description="Create overlapping windows retaining source-relative timestamps")
    p.add_argument("--input", required=True, type=pathlib.Path)
    p.add_argument("--output", required=True, type=pathlib.Path)
    p.add_argument("--window-seconds", type=float, default=180)
    p.add_argument("--overlap-seconds", type=float, default=30)
    args = p.parse_args()
    try:
        doc = json.loads(args.input.read_text(encoding="utf-8"))
        duration = checked_number(doc["duration_seconds"], "duration_seconds")
        if duration <= 0:
            raise ValueError("duration_seconds must be positive")
        if not (math.isfinite(args.window_seconds) and math.isfinite(args.overlap_seconds) and
                0 < args.window_seconds and 0 <= args.overlap_seconds < args.window_seconds):
            raise ValueError("Require finite window > 0 and 0 <= overlap < window")
        segments = doc["segments"]
        if not isinstance(segments, list):
            raise ValueError("segments must be an array")
        sanitized = []
        previous_start = -1
        for idx, s in enumerate(segments):
            start, end = checked_number(s["start"], "segment start"), checked_number(s["end"], "segment end")
            if not (0 <= start < end <= duration + 0.05) or start < previous_start:
                raise ValueError(f"Segment {idx} timestamps are out of order or out of bounds")
            if not isinstance(s.get("text"), str):
                raise ValueError(f"Segment {idx} must have text")
            sanitized.append({**s, "id": str(s.get("id", f"s{idx:05d}"))})
            previous_start = start
        windows = []
        cursor, step = 0.0, args.window_seconds - args.overlap_seconds
        while cursor < duration:
            upper = min(cursor + args.window_seconds, duration)
            included = [s for s in sanitized if s["start"] < upper and s["end"] > cursor]
            if included:
                windows.append({"window_id": f"w{len(windows):04d}", "start": round(cursor, 4),
                                "end": round(upper, 4), "segments": included})
            if upper == duration:
                break
            cursor += step
        output = {"video_id": doc.get("video_id", "unknown"), "duration_seconds": duration,
                  "window_seconds": args.window_seconds, "overlap_seconds": args.overlap_seconds,
                  "windows": windows}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Created {len(windows)} windows -> {args.output}")
        return 0
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Invalid transcript: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
