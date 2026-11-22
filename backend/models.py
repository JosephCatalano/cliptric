from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .captions import CaptionCustomization, CaptionPreset, clean_emphasis
from .editing import QualityOptions, mapped_overlays, retained_intervals
from .framing import FramingMode, SplitLayout, validate_framing
from .headlines import HeadlineOptions
from .limits import MAX_MEDIA_SECONDS
from .overlays import Overlays, layout

EmphasisWords = Annotated[list[str], Field(max_length=20), AfterValidator(clean_emphasis)]


class Recording(BaseModel):
    id: str
    name: str
    duration: float
    width: int
    height: int
    has_audio: bool
    size_bytes: int
    created_at: str


class CaptionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    presentation_version: int = Field(default=2, ge=1, le=2, strict=True)
    transcription_id: UUID
    revision: int = Field(ge=0, strict=True)
    position: Literal["auto", "lower", "centre", "upper"] = "lower"
    preset: CaptionPreset = "classic"
    emphasis_words: EmphasisWords = Field(default_factory=list)
    customization: CaptionCustomization = Field(default_factory=CaptionCustomization)


class ExportRequest(BaseModel):
    quality: QualityOptions = Field(default_factory=QualityOptions)
    overlays: Overlays = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    mode: FramingMode = "pad"
    split_layout: SplitLayout | None = None
    captions: CaptionOptions | None = None
    headline: HeadlineOptions | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> "ExportRequest":
        validate_framing(self.mode, self.split_layout)
        if self.end <= self.start:
            raise ValueError("End must be later than start.")
        if any(overlay.end > self.end - self.start for overlay in self.overlays):
            raise ValueError("Overlay timing must remain inside the trimmed clip.")
        kept = retained_intervals(self.start, self.end, self.quality)
        mapped = mapped_overlays(self.overlays, self.start, kept)
        if len(mapped) != len(self.overlays):
            raise ValueError(
                "An edit removes a text overlay entirely or leaves it too short. Adjust the overlay or restore that interval."
            )
        if self.quality.smart_framing and self.mode == "split":
            raise ValueError("Choose smart framing or the manual webcam/gameplay layout, not both.")
        layout(
            mapped,
            sum(b - a for a, b in kept),
            self.headline,
            self.captions.position if self.captions else None,
            split=self.mode == "split",
        )
        return self


class Clip(BaseModel):
    quality: QualityOptions = Field(default_factory=QualityOptions)
    overlays: Overlays = Field(default_factory=list)
    id: str
    recording_id: str
    start: float
    end: float
    mode: FramingMode
    split_layout: SplitLayout | None = None
    status: Literal["queued", "rendering", "cancelling", "succeeded", "failed", "cancelled"]
    error: str | None
    duration: float | None
    size_bytes: int | None
    created_at: str
    captions: CaptionOptions | None = None
    headline: HeadlineOptions | None = None


class SelectionDraft(BaseModel):
    quality: QualityOptions = Field(default_factory=QualityOptions)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    mode: FramingMode = "pad"
    split_layout: SplitLayout | None = None
    headline: HeadlineOptions | None = None
    captions_enabled: bool = Field(default=False, strict=True)
    caption_position: Literal["auto", "lower", "centre", "upper"] = "lower"
    caption_customization: CaptionCustomization = Field(default_factory=CaptionCustomization)
    caption_preset: CaptionPreset = "classic"
    emphasis_words: EmphasisWords = Field(default_factory=list)
    revision: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def valid_interval(self) -> "SelectionDraft":
        validate_framing(self.mode, self.split_layout)
        retained_intervals(self.start, self.end, self.quality)
        if self.quality.smart_framing and self.mode == "split":
            raise ValueError("Smart framing cannot be combined with a manual split layout.")
        if not 0 < self.end - self.start <= MAX_MEDIA_SECONDS:
            raise ValueError("Selection must have a positive duration of at most six hours.")
        return self
