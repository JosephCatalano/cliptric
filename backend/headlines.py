"""Validated opening headline cards, independent of spoken captions."""

import math
import textwrap
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .captions import _ass_text, _ass_time, _font_size


class HeadlineOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=120, strict=True)
    position: Literal["auto", "upper", "centre"] = "auto"
    appearance: Literal["classic", "cover"] = "cover"
    seconds: float = Field(default=8, ge=1, le=10, strict=True)
    fade_ms: int = Field(default=250, ge=0, le=1000, strict=True)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("Headline must contain readable text without control characters.")
        return value


def ass_document(styles: str, events: str) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\nWrapStyle: 2\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + styles
        + "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + events
    )


def headline_parts(
    options: HeadlineOptions, duration: float, caption_position=None
) -> tuple[str, str]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Headline requires a positive finite clip duration.")
    lines = textwrap.wrap(options.text.upper(), width=26, break_long_words=True)
    size = min(60, _font_size(lines))
    height = len(lines) * (size + 8) + 40
    position = options.position
    if position == "auto":
        position = "centre" if caption_position == "upper" else "upper"
    centre = 900 if options.appearance == "cover" else 760
    top = (
        (240 if options.appearance == "cover" else 180)
        if position == "upper"
        else centre - height // 2
    )
    bottom = top + height
    centiseconds = max(1, math.floor(min(duration, options.seconds) * 100))
    end = _ass_time(centiseconds)
    # Keep both fades within the actual quantized display interval, even on tiny clips.
    fade_ms = min(options.fade_ms, centiseconds * 5)
    fade_in = 0 if options.appearance == "cover" else fade_ms
    fade = f"\\fad({fade_in},{fade_ms})" if fade_ms else ""
    # Only validated numeric geometry enters override tags. User text is escaped.
    box = f"m 112 {top} l 968 {top} b 975 {top} 980 {top + 5} 980 {top + 12} l 980 {bottom - 12} b 980 {bottom - 5} 975 {bottom} 968 {bottom} l 112 {bottom} b 105 {bottom} 100 {bottom - 5} 100 {bottom - 12} l 100 {top + 12} b 100 {top + 5} 105 {top} 112 {top}"
    text = r"\N".join(_ass_text(line) for line in lines)
    style = f"Style: Headline,Arial,{size},&H00000000,&H00000000,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,100,100,0,1\n"
    events = (
        f"Dialogue: 0,0:00:00.00,{end},Headline,,0,0,0,,{{\\an7\\pos(0,0)\\1c&HFFFFFF&{fade}\\p1}}{box}\n"
        f"Dialogue: 1,0:00:00.00,{end},Headline,,0,0,0,,{{\\an5\\pos(540,{top + height // 2}){fade}}}{text}\n"
    )
    if options.appearance == "cover":
        # The cover is fully visible on frame zero. Decoration stays inside the
        # existing card region, with no extra crop or modification of source motion.
        accent = f"m 124 {bottom - 12} l 956 {bottom - 12} l 956 {bottom - 7} l 124 {bottom - 7}"
        events = (
            f"Dialogue: 0,0:00:00.00,{end},Headline,,0,0,0,,{{\\an7\\pos(0,8)\\1c&H000000&\\1a&H80&\\blur8{fade}\\p1}}{box}\n"
            f"Dialogue: 1,0:00:00.00,{end},Headline,,0,0,0,,{{\\an7\\pos(0,0)\\1c&HFFFFFF&{fade}\\p1}}{box}\n"
            f"Dialogue: 2,0:00:00.00,{end},Headline,,0,0,0,,{{\\an7\\pos(0,0)\\1c&HF65C8B&{fade}\\p1}}{accent}\n"
            f"Dialogue: 3,0:00:00.00,{end},Headline,,0,0,0,,{{\\an5\\pos(540,{top + height // 2}){fade}}}{text}\n"
        )
    return style, events


def headline_ass(options: HeadlineOptions, duration: float, caption_position=None) -> str:
    return ass_document(*headline_parts(options, duration, caption_position))


def write_headline(path: Path, options: HeadlineOptions, duration: float, caption_position=None):
    with path.open("x", encoding="utf-8-sig", newline="\n") as output:
        output.write(headline_ass(options, duration, caption_position))
