"""Bounded clip-relative text overlays and shared presentation collision checks."""

import math
import textwrap
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .captions import _ass_text, _ass_time, _font_size
from .headlines import HeadlineOptions, ass_document, headline_parts
from .limits import MAX_MEDIA_SECONDS


class TextOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=120, strict=True)
    kind: Literal["title", "headline", "cta"] = "title"
    start: float = Field(default=0, ge=0, le=MAX_MEDIA_SECONDS, strict=True)
    end: float = Field(gt=0, le=MAX_MEDIA_SECONDS, strict=True)
    position: Literal["auto", "upper", "centre", "lower"] = "auto"
    style: Literal["card", "outline", "violet"] = "card"
    _text = field_validator("text")(HeadlineOptions.clean_text.__func__)

    @model_validator(mode="after")
    def interval(self):
        if self.end - self.start < 0.1 - 1e-9:
            raise ValueError("Overlay must display for at least 0.1 seconds.")
        return self


Overlays = Annotated[list[TextOverlay], Field(max_length=6)]


def layout(overlays, duration, headline=None, caption_position=None, *, split=False):
    """Reserve whole safe regions conservatively, including the caption region."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Overlays require a positive finite clip duration.")
    if caption_position == "auto":
        caption_position = resolve_caption_position(
            caption_position, overlays, duration, headline, split
        )
    occupied = []
    if caption_position:
        occupied.append((0, duration, caption_position))
    if headline:
        position = headline.position
        if position == "auto":
            position = "centre" if caption_position == "upper" else "upper"
        occupied.append((0, min(duration, headline.seconds), position))
    result = []
    # Place explicit choices first so automatic text uses the remaining regions.
    for index, overlay in sorted(enumerate(overlays), key=lambda pair: pair[1].position == "auto"):
        if overlay.end > duration:
            raise ValueError("Overlay timing must remain inside the trimmed clip.")
        positions = (
            ("upper", "centre", "lower") if overlay.position == "auto" else (overlay.position,)
        )
        position = next(
            (
                p
                for p in positions
                if not any(
                    p == used and overlay.start < end and overlay.end > start
                    for start, end, used in occupied
                )
            ),
            None,
        )
        if position is None:
            raise ValueError(
                "Text overlays collide with captions or another overlay. Adjust timing or placement."
            )
        occupied.append((overlay.start, overlay.end, position))
        result.append((index, overlay, position))
    return [(overlay, position) for _, overlay, position in sorted(result)]


def resolve_caption_position(position, overlays, duration, headline=None, split=False):
    """Choose a free text region, keeping automatic captions below a split webcam."""
    if position != "auto":
        return position
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Caption placement requires a positive finite clip duration.")
    if any(overlay.end > duration for overlay in overlays):
        raise ValueError("Overlay timing must remain inside the trimmed clip.")
    for candidate in ("lower", "centre") if split else ("lower", "centre", "upper"):
        try:
            layout(overlays, duration, headline, candidate)
        except ValueError:
            continue
        if headline and headline.position == candidate:
            continue
        return candidate
    raise ValueError(
        "No caption region is free. Adjust overlay placement or timing before using Auto."
    )


def write_presentation(path: Path, overlays, duration, headline=None, caption_position=None):
    resolved = layout(overlays, duration, headline, caption_position)
    styles, events = headline_parts(headline, duration, caption_position) if headline else ("", "")
    for name, border in (("OverlayBox", 3), ("OverlayOutline", 1)):
        styles += f"Style: {name},Arial,60,&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,{border},0,0,5,100,100,0,1\n"
    for overlay, position in resolved:
        lines = textwrap.wrap(overlay.text, width=26, break_long_words=True)
        size = min(60, _font_size(lines))
        centre = {"upper": 360, "centre": 960, "lower": 1520}[position]
        start = _ass_time(math.floor(overlay.start * 100))
        end = _ass_time(math.floor(overlay.end * 100))
        text = r"\N".join(_ass_text(line) for line in lines)
        style = {
            "card": r"\1c&H000000&\3c&HFFFFFF&\bord12\shad0\3a&H00&\4a&HFF&",
            "violet": r"\1c&HFFFFFF&\3c&H8B2D6D&\bord12\shad0\3a&H00&\4a&HFF&",
            "outline": r"\1c&HFFFFFF&\3c&H101010&\bord4\shad1",
        }[overlay.style]
        # BorderStyle 3 produces an opaque text box; outline uses the original style.
        border = r"\xbord12\ybord12" if overlay.style != "outline" else ""
        style_name = "OverlayBox" if overlay.style != "outline" else "OverlayOutline"
        events += f"Dialogue: 2,{start},{end},{style_name},,0,0,0,,{{\\an5\\pos(540,{centre})\\fs{size}{style}{border}}}{text}\n"
    with path.open("x", encoding="utf-8-sig", newline="\n") as output:
        output.write(ass_document(styles, events))
