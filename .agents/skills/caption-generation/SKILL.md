---
name: caption-generation
description: Produce readable, correctly timed short-form subtitles for TikTok, Reels, and YouTube Shorts. Use for Whisper word timestamp alignment, source-to-clip timestamp rebasing, ASS/SRT caption formatting, safe text escaping, phrase grouping, subtitle burn-in handoff, and caption synchronization QA. Do not use to select highlights or claim captions are accurate without review.
---

# Caption generation for vertical clips

## Use this when

You need to create, style, time, adjust, or verify subtitles on a **known selected clip**. Transcription is an upstream stage; this skill uses word-level timestamps when available and asks for forced alignment or manual corrections when they aren't reliable. Prefer readability over maximal word animation in the MVP.

## Contract

- Input: original **source-absolute** word timestamps, clip `start` and `end` in source seconds, and a video profile (1080×1920 default).
- Output: ASS subtitles with **clip-relative** timestamps; resulting `t=0` corresponds to source clip start. Keep exact spoken content; do not rewrite speech for virality.
- Never generate invented words when transcription has low confidence; flag them for review.
- Keep on-screen phrases short and readable, normally 3–6 words with punctuation; change grouping for pauses and rhythm.
- Keep captions centered and high enough to avoid platform interface overlays. Check video-specific facecam/HUD conflicts.

## Procedure

1. Use Whisper/faster-whisper word timestamps or an aligned transcript. Inspect confidence/timing quality on sample clips.
2. Intersect source words with selected source interval and rebase times by subtracting clip start. Clamp partial words at boundaries.
3. Group into phrase events using punctuation, elapsed time, gap size, max words, and line length. The bundled script provides **static phrase captions**, not karaoke word highlighting.
4. Escape ASS override characters (especially braces and backslashes) so transcribed speech cannot inject drawing/timing commands.
5. Produce `.ass` with `scripts/make_ass.py`, then pass its path to video-processing's renderer using `--ass`.
6. Probe/render and visually review first/middle/end. Revisit alignment if subtitle drift is visible.
7. For later advanced styles, evaluate Remotion or ASS karaoke overrides only after basic alignment and positioning pass QA.

## Example

```powershell
python .github/skills/caption-generation/scripts/make_ass.py --input .github/skills/caption-generation/assets/words.json --clip-start 12 --clip-end 40 --output clip-001.ass
python .github/skills/video-processing/scripts/render_clip.py --input "C:\\videos\\stream.mp4" --start 12 --end 40 --output clip-001.mp4 --ass clip-001.ass
```

`make_ass.py` needs only Python standard library. Rendering needs an FFmpeg build with the `ass` filter enabled. See [timing, styles, and QA](references/TIMING_AND_STYLES.md).

## Acceptance criteria

- Zero captions before the start or after the end of the clip.
- Phrase timing derives from actual word timestamps; text is not fabricated.
- No subtitle events with negative or inverted timestamps.
- Readable subtitles in exported video; not obscured by major TikTok/Reels interface areas or the clip's focal action.
- Document transcription language, confidence limitations, and any manual corrections.
