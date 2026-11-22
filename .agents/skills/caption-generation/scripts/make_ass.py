#!/usr/bin/env python3
"""Generate clip-relative ASS phrase subtitles from source-absolute word times."""
import argparse
import json
import math
import pathlib
import re
import sys

ASS_HEADER = """[Script Info]
Title: AI Clipping Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H66000000,-1,0,0,0,100,100,0,0,1,4,1,2,90,90,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def value_float(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def ass_time(seconds):
    # ASS uses centiseconds; clamp negative rounding to zero.
    centiseconds = max(0, round(seconds * 100))
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, hundredths = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{hundredths:02d}"


def safe_text(value):
    # Remove potential ASS override markup rather than passing transcribed text as commands.
    return re.sub(r"\s+", " ", value.replace("\\", "/").replace("{", "(").replace("}", ")")).strip()


def phrase(words):
    result = " ".join(w["text"] for w in words)
    return re.sub(r"\s+([,.!?;:%])", r"\1", result)


def generate_events(words, clip_start, clip_end, max_words, max_chars, max_gap, max_seconds):
    selected = []
    previous_start = -1.0
    for index, item in enumerate(words):
        start, end = value_float(item.get("start"), f"words[{index}].start"), value_float(item.get("end"), f"words[{index}].end")
        if not 0 <= start < end or start < previous_start:
            raise ValueError(f"Invalid or out-of-order timestamps at word {index}")
        previous_start = start
        if end <= clip_start or start >= clip_end:
            continue
        token = item.get("text", item.get("word"))
        if not isinstance(token, str):
            raise ValueError(f"Word {index} has no text")
        token = safe_text(token)
        if token:
            selected.append({"start": max(0.0, start - clip_start),
                             "end": min(clip_end, end) - clip_start, "text": token})
    events, group = [], []
    for word in selected:
        if group and (len(group) >= max_words or
                      len(phrase(group + [word])) > max_chars or
                      word["end"] - group[0]["start"] > max_seconds or
                      word["start"] - group[-1]["end"] > max_gap):
            events.append(group)
            group = []
        group.append(word)
        if re.search(r"[.!?]$", word["text"]) and len(group) >= 2:
            events.append(group)
            group = []
    if group:
        events.append(group)
    return [(items[0]["start"], items[-1]["end"], phrase(items)) for items in events]


def main() -> int:
    p = argparse.ArgumentParser(description="Make clip-relative ASS captions from source-absolute word timings")
    p.add_argument("--input", required=True, type=pathlib.Path)
    p.add_argument("--output", required=True, type=pathlib.Path)
    p.add_argument("--clip-start", type=float, required=True)
    p.add_argument("--clip-end", type=float, required=True)
    p.add_argument("--max-words", type=int, default=5)
    p.add_argument("--max-chars", type=int, default=36)
    p.add_argument("--max-gap", type=float, default=0.5)
    p.add_argument("--max-seconds", type=float, default=3.0)
    args = p.parse_args()
    try:
        if not (math.isfinite(args.clip_start) and math.isfinite(args.clip_end) and
                0 <= args.clip_start < args.clip_end):
            raise ValueError("Require 0 <= clip-start < clip-end, both finite")
        if not (1 <= args.max_words <= 30 and 4 <= args.max_chars <= 200 and
                math.isfinite(args.max_gap) and 0 <= args.max_gap <= 10 and
                math.isfinite(args.max_seconds) and 0.05 <= args.max_seconds <= 30):
            raise ValueError("Caption grouping parameters are invalid")
        doc = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(doc.get("words"), list):
            raise ValueError("Input must contain a 'words' array")
        events = generate_events(doc["words"], args.clip_start, args.clip_end,
                                 args.max_words, args.max_chars, args.max_gap, args.max_seconds)
        lines = [ASS_HEADER]
        for start, end, text in events:
            if end <= start:
                continue
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}\n")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("".join(lines), encoding="utf-8-sig")
        print(f"Created {len(events)} caption groups -> {args.output}")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Caption generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
