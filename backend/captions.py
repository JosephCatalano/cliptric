"""Safe static and animated captions derived from editable source word timestamps."""

import math
import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CaptionPosition = Literal["auto", "lower", "centre", "upper"]
CaptionPreset = Literal["classic", "bold", "boxed", "flow", "glow", "impact", "studio", "focus"]
MAX_WORD_CHARS = 48
MAX_ANIMATED_EVENTS = 5000


class CaptionCustomization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grouping: Literal["auto", "one", "two", "three", "four"] = "auto"
    capitalization: Literal["original", "upper", "lower"] = "original"
    font_size: int = Field(default=80, ge=32, le=96, strict=True)
    font_family: Literal["Arial", "Georgia", "Courier New"] = "Arial"
    font_weight: Literal["preset", "regular", "bold"] = "preset"
    text_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    outline_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    shadow_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    background_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    emphasis_colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    background: Literal["preset", "none", "box"] = "preset"
    animation: Literal["preset", "none", "pop", "fade"] = "preset"
    profanity: Literal["keep", "symbols", "replace"] = "keep"
    censor_words: list[str] = Field(default_factory=list, max_length=20)
    replacement: str = Field(default="[censored]", min_length=1, max_length=24)

    @field_validator("censor_words")
    @classmethod
    def safe_words(cls, value: list[str]) -> list[str]:
        words = clean_emphasis([word.strip() for word in value if word.strip()])
        if any(not re.fullmatch(r"\w+(?:['’]\w+)*", word) for word in words):
            raise ValueError(
                "Censor entries must be individual words without surrounding punctuation."
            )
        return words

    @field_validator("replacement")
    @classmethod
    def safe_replacement(cls, value: str) -> str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("Replacement cannot contain control characters.")
        value = " ".join(value.split())
        if not value:
            raise ValueError("Replacement cannot be blank.")
        return value


DEFAULT_CENSOR_WORDS = ("fuck", "fucking", "fucked", "shit", "bullshit", "bitch", "asshole", "damn")


def censor_matches(text: str, customization: CaptionCustomization) -> list[str]:
    keys = {_emphasis_key(word) for word in (*DEFAULT_CENSOR_WORDS, *customization.censor_words)}
    return [
        match.group()
        for match in re.finditer(r"\w+(?:['’]\w+)*", text)
        if _emphasis_key(match.group()) in keys
    ]


def _display_text(text: str, customization: CaptionCustomization) -> str:
    if customization.profanity != "keep":
        matches = set(censor_matches(text, customization))
        text = re.sub(
            r"\w+(?:['’]\w+)*",
            lambda match: (
                (
                    "*" * len(match.group())
                    if customization.profanity == "symbols"
                    else customization.replacement
                )
                if match.group() in matches
                else match.group()
            ),
            text,
        )
    if customization.capitalization == "upper":
        return text.upper()
    if customization.capitalization == "lower":
        return text.lower()
    return text


def _colour(value: str | None, default: str, alpha: str = "00") -> str:
    if value is None:
        return default
    return f"&H{alpha}{value[5:7]}{value[3:5]}{value[1:3]}".upper()


def _separate_background(preset: CaptionPreset, customization: CaptionCustomization) -> bool:
    return customization.background == "box" or (
        preset in {"boxed", "studio"}
        and customization.background == "preset"
        and (
            preset == "studio"
            or customization.outline_colour is not None
            or customization.background_colour is not None
        )
    )


class CaptionError(ValueError):
    pass


@dataclass(frozen=True)
class CaptionWord:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str
    words: tuple[CaptionWord, ...] = field(default=(), compare=False, repr=False)


def _emphasis_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    first, last = 0, len(text)
    while first < last and not text[first].isalnum():
        first += 1
    while last > first and not text[last - 1].isalnum():
        last -= 1
    return text[first:last]


def clean_emphasis(words: Sequence[str]) -> list[str]:
    if isinstance(words, (str, bytes)) or not isinstance(words, Sequence) or len(words) > 20:
        raise CaptionError("Choose at most 20 individual words to emphasize.")
    result, seen = [], set()
    for text in words:
        if not isinstance(text, str):
            raise CaptionError("Emphasis words must be text.")
        text = text.strip()
        if not 0 < len(text) <= MAX_WORD_CHARS or any(
            char.isspace() or unicodedata.category(char) in {"Cc", "Cs"} for char in text
        ):
            raise CaptionError("Each emphasis entry must be one word of at most 48 characters.")
        key = _emphasis_key(text)
        if not key:
            raise CaptionError("Emphasis words must contain letters or numbers.")
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _timestamp(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionError("Caption timestamps must be finite numbers.")
    try:
        timestamp = float(value)
    except OverflowError as exc:
        raise CaptionError("Caption timestamps must be finite numbers.") from exc
    if not math.isfinite(timestamp):
        raise CaptionError("Caption timestamps must be finite numbers.")
    return timestamp


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise CaptionError("Each caption word must have text.")
    # Line breaks and ASCII control characters cannot introduce new ASS records.
    value = "".join(" " if char.isspace() else char for char in value)
    value = "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cs"})
    value = " ".join(value.split())
    if len(value) > MAX_WORD_CHARS:
        raise CaptionError("A caption word exceeds 48 characters. Shorten the correction.")
    return value


def _phrase(words: Sequence[CaptionWord]) -> str:
    return re.sub(r"\s+([,.!?;:%])", r"\1", " ".join(word.text for word in words))


def build_cues(
    words: Sequence[Mapping[str, object]],
    start: float,
    end: float,
    customization: CaptionCustomization | None = None,
) -> list[CaptionCue]:
    """Intersect ordered source words with a clip and group without inventing timing."""
    start, end = _timestamp(start), _timestamp(end)
    if not 0 <= start < end:
        raise CaptionError("Caption interval must have a nonnegative start and a later end.")
    selected: list[CaptionWord] = []
    customization = customization or CaptionCustomization()
    if sys.platform == "win32":
        # These curated Windows fonts are verified locally; never silently substitute
        # another family when an installation is missing its regular or bold face.
        stem = {"Arial": "arial", "Georgia": "georgia", "Courier New": "cour"}[
            customization.font_family
        ]
        bold = {"Arial": "arialbd", "Georgia": "georgiab", "Courier New": "courbd"}[
            customization.font_family
        ]
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        if not all((fonts / f"{name}.ttf").is_file() for name in (stem, bold)):
            raise CaptionError(
                "The selected caption font is missing. Install its regular and bold faces before exporting."
            )
    max_words = {"auto": 6, "one": 1, "two": 2, "three": 3, "four": 4}[customization.grouping]
    previous_start = 0.0
    for word in words:
        if not isinstance(word, Mapping):
            raise CaptionError("Each caption word must contain text and timestamps.")
        word_start, word_end = _timestamp(word.get("start")), _timestamp(word.get("end"))
        if not 0 <= word_start < word_end or word_start < previous_start:
            raise CaptionError("Caption words must have positive durations and ordered starts.")
        previous_start = word_start
        text = _text(word.get("text"))
        if not text or word_end <= start or word_start >= end:
            continue
        text = _display_text(text, customization)
        selected.append(
            CaptionWord(max(start, word_start) - start, min(end, word_end) - start, text)
        )

    cues: list[CaptionCue] = []
    group: list[CaptionWord] = []

    def flush() -> None:
        if group:
            # ASR may report genuinely overlapping words. Preserve their union rather
            # than shorten an earlier word when the last word has an earlier end.
            # If adjacent phrases overlap, libass's collision handling keeps both
            # visible without dropping text or manufacturing new word timestamps.
            cues.append(
                CaptionCue(
                    group[0].start, max(word.end for word in group), _phrase(group), tuple(group)
                )
            )
            group.clear()

    for word in selected:
        if group and (
            len(group) >= max_words
            or len(_phrase([*group, word])) > 48
            or max(word.end, *(part.end for part in group)) - group[0].start > 3
            or word.start - max(part.end for part in group) > 0.45
        ):
            flush()
        group.append(word)
        if len(group) >= 2 and re.search(r"[.!?][\"')\]]?$", word.text):
            flush()
    flush()
    return cues


def _lines(text: str) -> list[str]:
    if len(text) <= 24:
        return [text]
    spaces = [index for index, char in enumerate(text) if char == " "]
    if spaces:
        split = min(spaces, key=lambda index: max(index, len(text) - index - 1))
        if max(split, len(text) - split - 1) <= 28:
            return [text[:split], text[split + 1 :]]
    # A long uninterrupted correction still gets two lines, with no omitted characters.
    split = len(text) // 2
    return [text[:split].rstrip(), text[split:].lstrip()]


def _ass_text(text: str) -> str:
    # libass interprets backslash escapes and brace blocks. Fullwidth equivalents
    # retain the visible punctuation without letting transcript text become markup.
    replacements = str.maketrans({"\\": "＼", "{": "｛", "}": "｝"})
    return text.translate(replacements)


def _font_size(lines: Sequence[str], maximum: int = 64) -> int:
    # Conservative glyph widths keep unusually long corrections inside the safe area.
    # Caption callers supply the configured maximum; dense text scales down as needed.
    def width(line: str) -> float:
        return sum(
            1.0
            if unicodedata.east_asian_width(char) in {"W", "F"} or char in "MWmw@%"
            else 0.8
            if char.isupper()
            else 0.65
            if char.isalnum()
            else 0.4
            for char in line
        )

    return min(maximum, math.floor(850 / max(width(line) for line in lines)))


def _ass_time(centiseconds: int) -> str:
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"


def write_ass(
    words: Sequence[Mapping[str, object]],
    start: float,
    end: float,
    path: Path,
    position: CaptionPosition = "lower",
    preset: CaptionPreset = "classic",
    emphasis_words: Sequence[str] = (),
    customization: CaptionCustomization | None = None,
    presentation_version: int = 2,
) -> int:
    """Create a new ASS snapshot and return its renderable event count."""
    if type(presentation_version) is not int or presentation_version not in (1, 2):
        raise CaptionError("Unsupported caption presentation version.")
    positions = {
        "auto": (2, 360),
        "lower": (2, 360),
        "centre": (5, 0),
        "upper": (8, 350 if presentation_version == 2 else 260),
    }
    if position not in positions:
        raise CaptionError("Caption position must be lower, centre, or upper.")
    alignment, margin = positions[position]
    styles = {
        "classic": ("&H00FFFFFF", 1, 4, 1),
        "bold": ("&H0000EFFF", 1, 6, 2),
        "boxed": ("&H00FFFFFF", 3, 10, 0),
        "flow": ("&H00FFFFFF", 1, 4, 1),
        "glow": ("&H00FFFFFF", 1, 4, 1),
        "impact": ("&H00FFFFFF", 1, 5, 2),
        "studio": ("&H00FFFFFF", 1, 3, 1),
        "focus": ("&H00FFFFFF", 1, 4, 1),
    }
    if preset not in styles:
        raise CaptionError("Unknown caption preset.")
    colour, border, outline, shadow = styles[preset]
    emphasis = {_emphasis_key(word) for word in clean_emphasis(emphasis_words)}
    weight = 0 if emphasis else -1
    customization = customization or CaptionCustomization()
    if customization.font_weight != "preset":
        weight = -1 if customization.font_weight == "bold" else 0
    animated = preset in {"flow", "glow", "focus"} and customization.animation == "preset"
    colour = _colour(customization.text_colour, colour)
    outline_colour = _colour(customization.outline_colour, "&H00101010")
    shadow_colour = _colour(customization.shadow_colour, "&H80000000", "80")
    separate_background = _separate_background(preset, customization)
    if customization.background != "preset" or separate_background:
        border = 1
        if preset == "boxed":
            outline = 4
    cues = build_cues(words, start, end, customization)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "ScaledBorderAndShadow: yes\nWrapStyle: 2\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{customization.font_family},{customization.font_size},{colour},&H00FFFFFF,{outline_colour},{shadow_colour},"
        f"{weight},0,0,0,100,100,0,0,{border},{outline},{shadow},{alignment},100,100,{margin},1\n"
        f"Style: Background,{customization.font_family},{customization.font_size},&HFF000000,&HFF000000,"
        f"{_colour(customization.background_colour, '&H304C2234' if preset == 'studio' else '&H80000000', '80')},&HFF000000,"
        f"{weight},0,0,0,100,100,0,0,3,10,0,{alignment},100,100,{margin},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    for cue in cues:
        # Round inward: quantization must never show text outside its speech interval.
        first = math.ceil(cue.start * 100 - 1e-8)
        last = math.floor(cue.end * 100 + 1e-8)
        if first >= last:
            raise CaptionError(
                "A caption phrase is too short to render at 0.01-second precision. "
                "Adjust the clip boundary or hide that word."
            )
        lines = _lines(cue.text)
        safe_lines = [_ass_text(line) for line in lines]
        size = _font_size(safe_lines, customization.font_size)
        states = _timed_states(cue, first, last) if animated else [(first, last, frozenset())]
        for begin, finish, active in states:
            if animated or emphasis:
                text = _styled_text(
                    cue, lines, size, active, emphasis, preset, finish - begin, customization
                )
            else:
                text = "\\N".join(safe_lines)
                if size < customization.font_size:
                    text = "{\\fs" + str(size) + "}" + text
            duration_ms = (finish - begin) * 10
            effect = ""
            animation = customization.animation
            if animation == "preset":
                animation = {"impact": "pop", "studio": "fade"}.get(preset, "none")
            if animation == "fade":
                fade = min(120, duration_ms // 3)
                effect = f"{{\\fad({fade},{fade})}}"
            elif animation == "pop":
                effect = (
                    "{\\fscx85\\fscy85\\t(0,"
                    + str(min(120, duration_ms // 2))
                    + ",\\fscx100\\fscy100)}"
                )
            if separate_background:
                background_text = "{\\fs" + str(size) + "}" + "\\N".join(safe_lines)
                events.append(
                    f"Dialogue: 0,{_ass_time(begin)},{_ass_time(finish)},Background,,0,0,0,,{effect}{background_text}\n"
                )
            events.append(
                f"Dialogue: 1,{_ass_time(begin)},{_ass_time(finish)},Default,,0,0,0,,{effect}{text}\n"
            )
            if animated and len(events) > MAX_ANIMATED_EVENTS:
                raise CaptionError(
                    "Caption timing is too dense for animation. Use a static preset."
                )
    with path.open("x", encoding="utf-8-sig", newline="\n") as output:
        output.write(header)
        output.writelines(events)
    return len(events)


def _timed_states(cue: CaptionCue, first: int, last: int) -> list[tuple[int, int, frozenset[int]]]:
    """Partition actual word boundaries, keeping pauses and simultaneous words."""
    intervals = []
    boundaries = {first, last}
    for index, word in enumerate(cue.words):
        begin = max(first, math.ceil(word.start * 100 - 1e-8))
        finish = min(last, math.floor(word.end * 100 + 1e-8))
        if begin < finish:
            intervals.append((begin, finish, index))
            boundaries.update((begin, finish))
        # Sub-centisecond words remain readable in the phrase without invented timing.
    ordered = sorted(boundaries)
    return [
        (a, b, frozenset(index for begin, finish, index in intervals if begin <= a < finish))
        for a, b in zip(ordered, ordered[1:])
    ]


def _styled_text(
    cue: CaptionCue,
    lines: Sequence[str],
    size: int,
    active: frozenset[int],
    emphasis: set[str],
    preset: CaptionPreset,
    duration_cs: int,
    customization: CaptionCustomization,
) -> str:
    # Associate displayed characters with their original timed word. Normalize only
    # the same spaces before punctuation as _phrase; never infer timing from text.
    characters = []
    for index, word in enumerate(cue.words):
        if characters:
            characters.append((" ", None))
        for char in word.text:
            if char in ",.!?;:%":
                while characters and characters[-1][0] == " ":
                    characters.pop()
            characters.append((char, index))
    if "".join(char for char, _ in characters) != cue.text:
        raise CaptionError("Caption word layout could not be preserved.")
    safe_text = _ass_text(cue.text)
    marked = set()
    for token in re.finditer(r"\S+", cue.text):
        if _emphasis_key(token.group()) in emphasis:
            marked.update(range(token.start(), token.end()))
    for token in re.finditer(r"\w+(?:['’]\w+)*", cue.text):
        if _emphasis_key(token.group()) in emphasis:
            marked.update(range(token.start(), token.end()))
    weight = 0 if emphasis else -1
    if customization.font_weight != "preset":
        weight = -1 if customization.font_weight == "bold" else 0
    base_colour = _colour(
        customization.text_colour, "&H0000EFFF" if preset == "bold" else "&H00FFFFFF"
    )
    base_outline = _colour(customization.outline_colour, "&H00101010")
    border_width = {"bold": 6, "impact": 5, "studio": 3}.get(preset, 4)
    if (
        preset == "boxed"
        and customization.background == "preset"
        and not _separate_background(preset, customization)
    ):
        border_width = 10
    reset = (
        f"{{\\fs{size}\\b{weight}\\1c{base_colour}&\\3c{base_outline}&"
        + f"\\bord{border_width}\\blur0}}"
    )
    rise = min(80, duration_cs * 10)
    rendered = []
    for line_index, line in enumerate(lines):
        offset = 0 if line_index == 0 else len(cue.text) - len(line)
        parts = []
        previous = object()
        for char_index, (_, owner) in enumerate(characters[offset : offset + len(line)]):
            emphasized = offset + char_index in marked
            run = (owner, emphasized)
            if run != previous:
                parts.append(reset)
                if emphasized:
                    parts.append(
                        "{\\b1\\1c" + _colour(customization.emphasis_colour, "&H0000EFFF") + "&}"
                    )
                if owner in active:
                    if preset in {"flow", "focus"} and not emphasized:
                        parts.append(
                            "{\\t(0,"
                            + str(rise)
                            + ",\\1c"
                            + _colour(
                                customization.emphasis_colour,
                                "&H00EEDD66" if preset == "focus" else "&H0000EFFF",
                            )
                            + "&)}"
                        )
                    elif preset == "glow":
                        parts.append(
                            "{\\3c"
                            + _colour(customization.emphasis_colour, "&H00FF99BB")
                            + "&\\bord5\\blur2\\t(0,"
                            + str(rise)
                            + ",\\bord8\\blur3)}"
                        )
                previous = run
            parts.append(safe_text[offset + char_index])
        rendered.append("".join(parts))
    return "\\N".join(rendered)
