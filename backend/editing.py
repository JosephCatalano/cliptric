"""Reversible source-to-output edit mapping and immutable output-quality options."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .framing import SourceRegion
from .limits import MAX_MEDIA_SECONDS


class Removal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    reason: Literal["silence", "filler", "manual"]
    enabled: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def interval(self):
        if self.end - self.start < 0.02:
            raise ValueError("A removal must span at least 0.02 seconds.")
        return self


class FramingPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    at: float = Field(ge=0, strict=True)
    x: float = Field(ge=0, le=1, strict=True)
    y: float = Field(ge=0, le=1, strict=True)


class SmartFraming(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    detector: Literal["faces", "motion"]
    strategy: Literal["crop", "fit"]
    width: float = Field(ge=0.01, le=1, strict=True)
    height: float = Field(ge=0.01, le=1, strict=True)
    points: list[FramingPoint] = Field(min_length=1, max_length=24)
    protected_regions: list[SourceRegion] = Field(default_factory=list, max_length=4)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_path(self):
        if any(a.at >= b.at for a, b in zip(self.points, self.points[1:])):
            raise ValueError("Framing points must have increasing source times.")
        for point in self.points:
            if point.x + self.width > 1 + 1e-8 or point.y + self.height > 1 + 1e-8:
                raise ValueError("Smart framing must stay inside the source.")
            if self.strategy == "crop" and any(
                region.x < point.x - 1e-8
                or region.y < point.y - 1e-8
                or region.x + region.width > point.x + self.width + 1e-8
                or region.y + region.height > point.y + self.height + 1e-8
                for region in self.protected_regions
            ):
                raise ValueError("A crop cannot discard a protected source region.")
        if self.strategy == "fit" and (self.width, self.height) != (1, 1):
            raise ValueError("Fit framing must preserve the full source.")
        return self


class QualityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cleanup: Literal["off", "light", "standard"] = "off"
    normalize: bool = Field(default=False, strict=True)
    removals: list[Removal] = Field(default_factory=list, max_length=40)
    smart_framing: SmartFraming | None = None
    cover_at: float | None = Field(default=None, ge=0, le=MAX_MEDIA_SECONDS, strict=True)


def retained_intervals(
    start: float, end: float, options: QualityOptions
) -> list[tuple[float, float]]:
    if not all(math.isfinite(value) for value in (start, end)) or not 0 <= start < end:
        raise ValueError("Invalid source interval for editing.")
    cuts = sorted((cut.start, cut.end) for cut in options.removals if cut.enabled)
    previous = start
    kept = []
    for first, last in cuts:
        if first < previous - 1e-8 or last > end + 1e-8:
            raise ValueError(
                "Enabled removals must be ordered, non-overlapping, and inside the clip."
            )
        if first - previous >= 0.02:
            kept.append((previous, first))
        elif first > previous + 1e-8:
            raise ValueError("An edit would leave a fragment shorter than 0.02 seconds.")
        previous = last
    if end - previous >= 0.02:
        kept.append((previous, end))
    elif end > previous + 1e-8:
        raise ValueError("An edit would leave a fragment shorter than 0.02 seconds.")
    duration = sum(last - first for first, last in kept)
    if duration < max(0.1, (end - start) * 0.5):
        raise ValueError(
            "Keep at least half the clip and 0.1 seconds; shorten the source selection for larger edits."
        )
    path = options.smart_framing
    if options.cover_at is not None and not any(
        first <= options.cover_at < last for first, last in kept
    ):
        raise ValueError(
            "The cover frame must remain inside retained footage. Clear the cover or choose another frame after trimming."
        )
    if path and (abs(path.points[0].at - start) > 1e-8 or path.points[-1].at > end + 1e-8):
        raise ValueError(
            "Framing points must start at the clip boundary and remain within it. Reanalyze after trimming."
        )
    return kept


def output_time(source: float, kept: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(source, last) - first) for first, last in kept)


def mapped_words(words: list[dict], kept: list[tuple[float, float]], base: float) -> list[dict]:
    result = []
    for word in words:
        intersections = [
            (max(word["start"], first), min(word["end"], last))
            for first, last in kept
            if word["start"] < last and word["end"] > first
        ]
        if intersections:
            first, last = intersections[0][0], intersections[-1][1]
            begin, finish = output_time(first, kept), output_time(last, kept)
            if finish > begin:
                result.append({**word, "start": base + begin, "end": base + finish})
    return result


def mapped_overlays(overlays, source_start: float, kept: list[tuple[float, float]]):
    result = []
    for overlay in overlays:
        first = output_time(source_start + overlay.start, kept)
        last = output_time(source_start + overlay.end, kept)
        if last - first >= 0.1 - 1e-9:
            result.append(overlay.model_copy(update={"start": first, "end": last}))
    return result


def edit_filter(kept, source_start, video_index, audio_index, options):
    """Map video timestamps continuously; concatenate sample-exact audio intervals.

    Independent video segment concatenation accumulates frame-rounding errors at
    every cut. Selecting source frames and mapping their original PTS avoids drift.
    """
    changed = len(kept) != 1 or kept[0][0] != source_start
    selection = "+".join(
        f"gte(t,{a - source_start:.6f})*lt(t,{b - source_start:.6f})" for a, b in kept
    )
    timing = "+".join(f"clip(PTS*TB-{a - source_start:.6f},0,{b - a:.6f})" for a, b in kept)
    parts = [f"[0:{video_index}]select='{selection}',setpts='({timing})/TB'[edited_video]"]
    count = len(kept)
    if count > 1 and audio_index is not None:
        parts.append(f"[0:{audio_index}]asplit={count}" + "".join(f"[sa{i}]" for i in range(count)))
    for index, (first, last) in enumerate(kept):
        begin, finish = first - source_start, last - source_start
        if audio_index is not None:
            audio = f"[sa{index}]" if count > 1 else f"[0:{audio_index}]"
            length = last - first
            fades = (
                f",afade=t=in:d=0.005,afade=t=out:st={max(0, length - 0.005):.6f}:d=0.005"
                if count > 1 or changed
                else ""
            )
            parts.append(
                f"{audio}atrim=start={begin:.6f}:end={finish:.6f},asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={length:.6f},atrim=duration={length:.6f}{fades}[a{index}]"
            )
    if audio_index is not None:
        parts.append(
            "".join(f"[a{i}]" for i in range(count)) + f"concat=n={count}:v=0:a=1[joined_audio]"
        )
        filters = []
        if options.cleanup != "off":
            filters += [
                "highpass=f=70",
                f"afftdn=nr={6 if options.cleanup == 'light' else 12}:nf=-50",
            ]
        if options.normalize:
            filters += ["loudnorm=I=-16:TP=-1.5:LRA=11", "aresample=48000"]
        parts.append(
            "[joined_audio]" + (",".join(filters) if filters else "anull") + "[edited_audio]"
        )
    return ";".join(parts) + ";"


def smart_filter(options: SmartFraming, kept, width: int, height: int) -> str:
    crop_w = max(2, int(width * options.width) // 2 * 2)
    crop_h = max(2, int(height * options.height) // 2 * 2)
    for point in options.points:
        x = min(width - crop_w, int(point.x * width) // 2 * 2)
        y = min(height - crop_h, int(point.y * height) // 2 * 2)
        if any(
            region.x * width < x
            or region.y * height < y
            or (region.x + region.width) * width > x + crop_w
            or (region.y + region.height) * height > y + crop_h
            for region in options.protected_regions
        ):
            raise ValueError(
                "The pixel-aligned crop would lose protected content. Use fit framing or expand the protected region and reanalyze."
            )

    # Piecewise linear motion uses only finite validated numbers. A fixed crop size
    # avoids zoom pumping; endpoints are remapped through accepted timeline cuts.
    def expression(axis, maximum):
        points = []
        for point in options.points:
            at = output_time(point.at, kept)
            value = min(maximum, max(0, getattr(point, axis) * (width if axis == "x" else height)))
            if points and abs(points[-1][0] - at) < 1e-8:
                points[-1] = (at, value)
            else:
                points.append((at, value))
        value = f"{points[-1][1]:.3f}"
        # Piecewise bounds are controlled; short plans avoid FFmpeg parser depth limits.
        for (a, x), (b, y) in reversed(list(zip(points, points[1:]))):
            value = (
                f"if(lt(t,{b:.4f}),{x:.3f}+({y - x:.3f})*clip((t-{a:.4f})/{b - a:.4f},0,1),{value})"
            )
        return "'" + value + "'"

    return f"crop={crop_w}:{crop_h}:x={expression('x', width - crop_w)}:y={expression('y', height - crop_h)},scale=1080:1920,setsar=1"
