#!/usr/bin/env python3
"""Validate source-grounded highlight candidate JSON from an untrusted model."""
import argparse
import json
import math
import pathlib
import re
import sys


def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def validate(doc, duration, transcript, min_len, max_len):
    candidates = doc.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("candidates must be an array")
    ids = set()
    problems = []
    transcript_text = normalized(" ".join(s.get("text", "") for s in transcript.get("segments", []))) if transcript else None
    for idx, c in enumerate(candidates):
        prefix = f"Candidate #{idx + 1}"
        try:
            if not isinstance(c, dict):
                raise ValueError("candidate must be an object")
            cid = c["id"]
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError("id must be nonempty string")
            if cid in ids:
                raise ValueError(f"duplicate id: {cid}")
            ids.add(cid)
            start, end = number(c["start"], "start"), number(c["end"], "end")
            if not 0 <= start < end <= duration + 0.05:
                raise ValueError(f"times {start}–{end} outside source duration {duration}")
            length = end - start
            if length < min_len or length > max_len:
                raise ValueError(f"length {length:.2f}s outside allowed [{min_len}, {max_len}]s")
            score = number(c["score"], "score")
            if not 0 <= score <= 100:
                raise ValueError("score must be between 0 and 100")
            for field in ("title", "reason", "evidence", "category"):
                if not isinstance(c.get(field), str) or not c[field].strip():
                    raise ValueError(f"{field} must be nonempty string")
            if transcript_text is not None:
                # Exact evidence is a deliberate check against invented speech.
                if normalized(c["evidence"]) not in transcript_text:
                    raise ValueError("evidence is not a verbatim contiguous passage in transcript")
                relevant = [s for s in transcript["segments"] if s["start"] < end and s["end"] > start]
                if not relevant or normalized(c["evidence"]) not in normalized(" ".join(s["text"] for s in relevant)):
                    raise ValueError("evidence exists elsewhere, but not in proposed time interval")
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"{prefix}: {exc}")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description="Validate candidate intervals and verbatim transcript evidence")
    p.add_argument("--input", required=True, type=pathlib.Path)
    p.add_argument("--duration", required=True, type=float)
    p.add_argument("--transcript", type=pathlib.Path)
    p.add_argument("--min-length", type=float, default=8.0)
    p.add_argument("--max-length", type=float, default=120.0)
    args = p.parse_args()
    try:
        doc = json.loads(args.input.read_text(encoding="utf-8"))
        transcript = json.loads(args.transcript.read_text(encoding="utf-8")) if args.transcript else None
        if not math.isfinite(args.duration) or args.duration <= 0:
            raise ValueError("--duration must be positive and finite")
        if not (math.isfinite(args.min_length) and math.isfinite(args.max_length) and 0 < args.min_length <= args.max_length):
            raise ValueError("Require 0 < --min-length <= --max-length")
        if transcript and doc.get("video_id") != transcript.get("video_id"):
            raise ValueError("Candidate video_id does not match transcript video_id")
        problems = validate(doc, args.duration, transcript, args.min_length, args.max_length)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"Valid: {len(doc['candidates'])} candidates; source duration {args.duration}s")
        return 0
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
