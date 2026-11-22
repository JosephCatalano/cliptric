"""Validated source-relative regions for the fixed webcam/gameplay composition."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FramingMode = Literal["pad", "crop", "split"]


class SourceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=0.99, strict=True)
    y: float = Field(ge=0, le=0.99, strict=True)
    width: float = Field(ge=0.01, le=1, strict=True)
    height: float = Field(ge=0.01, le=1, strict=True)

    @model_validator(mode="after")
    def inside_source(self):
        if self.x + self.width > 1 + 1e-9 or self.y + self.height > 1 + 1e-9:
            raise ValueError("Crop region must remain inside the source picture.")
        return self

    def pixels(self, width: int, height: int, target: tuple[int, int] | None = None) -> str:
        # Align to chroma samples; never expand the requested region beyond the source.
        x, y = int(self.x * width) // 2 * 2, int(self.y * height) // 2 * 2
        w = min(int(self.width * width) // 2 * 2, width - x)
        h = min(int(self.height * height) // 2 * 2, height - y)
        if w < 2 or h < 2:
            raise ValueError("Selected crop is smaller than two source pixels. Enlarge the region.")
        if target:
            # Crop before enlargement so thin selections cannot create enormous frames.
            target_w, target_h = target
            cropped_w = min(w, max(2, int(h * target_w / target_h) // 2 * 2))
            cropped_h = min(h, max(2, int(w * target_h / target_w) // 2 * 2))
            x += (w - cropped_w) // 4 * 2
            y += (h - cropped_h) // 4 * 2
            w, h = cropped_w, cropped_h
        return f"{w}:{h}:{x}:{y}"


class SplitLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webcam: SourceRegion
    gameplay: SourceRegion
    gameplay_fit: Literal["fit", "fill"] = "fit"


def validate_framing(
    mode: FramingMode | None, options: SplitLayout | None, crop: SourceRegion | None = None
) -> None:
    if (mode == "split") != (options is not None):
        raise ValueError(
            "Webcam/gameplay framing requires its crop regions; other modes must omit them."
        )
    if crop is not None and mode != "crop":
        raise ValueError("A manual crop region requires Fill frame layout.")


def split_filter(video_index: int, width: int, height: int, options: SplitLayout) -> str:
    # Coordinates refer to the full display-oriented picture, before border detection.
    width, height = width // 2 * 2, height // 2 * 2
    camera = options.webcam.pixels(width, height, (1080, 640))
    gameplay = options.gameplay.pixels(width, height)
    gameplay_cover = options.gameplay.pixels(width, height, (1080, 1280))
    branches = (
        "split=3[cam_source][game_source][back_source]"
        if options.gameplay_fit == "fit"
        else "split=2[cam_source][game_source]"
    )
    graph = (
        f"[0:{video_index}]scale={width}:{height},setsar=1,{branches};"
        f"[cam_source]crop={camera},"
        "scale=1080:640,setsar=1[cam];"
    )
    if options.gameplay_fit == "fill":
        graph += f"[game_source]crop={gameplay_cover},scale=1080:1280,setsar=1[game];"
    else:
        graph += (
            f"[back_source]crop={gameplay_cover},scale=270:320,setsar=1,boxblur=12:2,scale=1080:1280[game_blur];"
            f"[game_source]crop={gameplay},scale=1080:1280:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1[game_fit];"
            "[game_blur][game_fit]overlay=(W-w)/2:(H-h)/2:shortest=1[game];"
        )
    return graph + "[cam][game]vstack=inputs=2:shortest=1"
