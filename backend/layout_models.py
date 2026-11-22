"""Server-generated per-clip layout decisions, separate from editable settings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .framing import SourceRegion, SplitLayout, validate_framing

LAYOUT_VERSION = 1


class AutoLayoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = LAYOUT_VERSION
    context: str = Field(default="", max_length=64, pattern="^[a-f0-9]*$")
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    mode: Literal["pad", "crop", "split", "follow"]
    crop_region: SourceRegion | None = None
    split_layout: SplitLayout | None = None
    headline_position: Literal["upper", "centre"] | None = None
    summary: str = Field(min_length=1, max_length=300)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        if self.end <= self.start:
            raise ValueError("Auto layout requires a positive source interval.")
        validate_framing(self.mode, self.split_layout, self.crop_region)
        if self.mode == "crop" and self.crop_region is None:
            raise ValueError("A resolved webcam crop needs its source region.")
        return self
