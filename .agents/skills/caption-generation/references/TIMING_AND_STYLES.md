# Caption timing and styling guide

## Input format

```json
{
  "video_id": "stream-001",
  "words": [
    {"start": 12.0, "end": 12.35, "text": "No"},
    {"start": 12.36, "end": 12.65, "text": "way!"}
  ]
}
```

Times are **seconds from the original source**, not the clip. `make_ass.py --clip-start 12 --clip-end 40` produces events relative to a 28-second clip.

## Timing

- Avoid blind evenly spaced captions: follow word timestamps.
- Prefer natural chunks: approximately 2–6 words, 0.6–3.0 seconds, punctuation aware; adapt to speech speed.
- For a word crossing the cut boundary, clamp the event to the visible portion; if timing is suspicious, flag rather than silently inventing timing.
- Language models may improve punctuation, but must not substitute words without source evidence.
- Drift can come from resampling, VFR, stitched inputs, or transcription offsets. Verify at the beginning, middle, and end.

## Visual rules

- 1080×1920; high contrast text with dark outline is safer than unoutlined white.
- Keep captions within a reasonably safe center-lower region, rather than flush to bottom (UI overlays differ by platform).
- For gameplay, don't obscure minimaps, HUD, or gameplay focal action; adjust position per template.
- Test readability on a phone at normal viewing size.
- Captions are speech accessibility features; avoid heavy flashing, excessive motion, and misleading emphasis.

## What the bundled generator does and doesn't do

**Does:** build valid ASS events, compute clip-relative times from source word timestamps, group short text phrases, escape unsafe markup characters, and emit a default white-with-outline style.

**Doesn't:** transcribe audio, diarize speakers, detect faces, generate word-by-word karaoke animations, automatically position around faces/HUD, or guarantee word alignment. Add those separately and label them honestly.

## QA examples

- Caption appears before first audible word → source/clip offset mismatch.
- Caption stays visible well after speech ends → phrase grouping limits too generous.
- Curly braces cause formatting glitches → unsafe ASS text escaping.
- Caption looks correct in ASS but missing in MP4 → FFmpeg lacks libass, wrong filter path, or an actual render failure.
