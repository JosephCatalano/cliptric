"""Saved project contracts. All media times are source-relative seconds."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ai_instructions import AIInstructions
from .captions import CaptionCustomization, CaptionPreset
from .editing import QualityOptions, mapped_overlays, retained_intervals
from .framing import FramingMode, SourceRegion, SplitLayout, validate_framing
from .headlines import HeadlineOptions
from .highlight_models import EditorialQuality, SourceRange
from .limits import MAX_MEDIA_SECONDS
from .models import Clip, EmphasisWords
from .overlays import Overlays, layout
from .transcription_models import clean_text

ACTIVE = {"queued", "downloading", "transcribing", "analysing", "rendering", "cancelling"}
Preset = Literal["none"] | CaptionPreset


class ProjectSettingsDraft(BaseModel):
    """Bounded fields for unfinished setup; creation enforces combined settings."""

    batch_mode: Literal["keep", "auto", "highlights"] = "keep"
    clips_per_source: int = Field(default=3, ge=1, le=5, strict=True)
    presentation_mode: Literal["off", "auto", "talking_head", "podcast", "gameplay"] = "off"
    ai_instructions: AIInstructions = ""
    quality_mode: bool = Field(default=False, strict=True)
    audio_cleanup: Literal["off", "light", "standard"] = "off"
    normalize_audio: bool = Field(default=False, strict=True)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    analysis_start: float = Field(default=0, ge=0, strict=True)
    analysis_end: float = Field(gt=0, strict=True)
    duration_mode: Literal["auto", "manual", "full"] = "auto"
    headline_enabled: bool = Field(default=True, strict=True)
    headline_position: Literal["auto", "upper", "centre"] = "auto"
    min_seconds: int = Field(default=15, ge=8, le=MAX_MEDIA_SECONDS, strict=True)
    max_seconds: int = Field(default=60, ge=8, le=MAX_MEDIA_SECONDS, strict=True)
    max_clips: int = Field(default=5, ge=1, le=20, strict=True)
    caption_preset: Preset = "classic"
    caption_customization: CaptionCustomization = Field(default_factory=CaptionCustomization)
    emphasis_words: EmphasisWords = Field(default_factory=list)
    caption_position: Literal["auto", "lower", "centre", "upper"] = "lower"
    framing: FramingMode = "pad"
    split_layout: SplitLayout | None = None
    crop_region: SourceRegion | None = None


class ProjectSettings(ProjectSettingsDraft):
    @model_validator(mode="after")
    def valid_range(self):
        validate_framing(self.framing, self.split_layout, self.crop_region)
        minimum = (
            0.1
            if self.duration_mode == "full"
            else (8 if self.duration_mode == "auto" else self.min_seconds)
        )
        if self.analysis_end - self.analysis_start < minimum:
            raise ValueError("Analysis range must fit the minimum clip duration (Auto: 8 seconds).")
        if self.min_seconds > self.max_seconds:
            raise ValueError("Minimum clip duration cannot exceed maximum duration.")
        return self


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID  # Client-generated idempotency token, reused after an uncertain HTTP outcome.
    recording_id: UUID | None = None
    youtube_source_id: UUID | None = None
    recording_ids: list[UUID] = Field(default_factory=list, max_length=15)
    generate_titles: bool = Field(default=False, strict=True)
    remote_consent: bool = Field(default=False, strict=True)
    title: str = Field(min_length=1, max_length=120, strict=True)
    settings: ProjectSettings

    @model_validator(mode="after")
    def one_source(self):
        if (
            sum(
                (
                    self.recording_id is not None,
                    self.youtube_source_id is not None,
                    bool(self.recording_ids),
                )
            )
            != 1
        ):
            raise ValueError(
                "Choose one recording, one YouTube preview, or a batch of supplied clips."
            )
        if len(set(self.recording_ids)) != len(self.recording_ids):
            raise ValueError("Each supplied recording must appear only once.")
        if bool(self.recording_ids) != (self.settings.duration_mode == "full"):
            raise ValueError("Full-length processing requires a batch of supplied clips.")
        if self.generate_titles and (not self.recording_ids or not self.remote_consent):
            raise ValueError("AI batch titles require explicit consent to share transcript text.")
        if self.settings.batch_mode in {"auto", "highlights"} and (
            not self.recording_ids or not self.remote_consent
        ):
            raise ValueError(
                "AI batch review requires supplied videos and explicit transcript-sharing consent."
            )
        return self

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str):
        value = clean_text(value)
        if not value:
            raise ValueError("Title cannot be blank.")
        return value


class ProjectItem(BaseModel):
    transcription_backend: Literal["cpu", "cuda"] | None = None
    posted: bool = Field(default=False, strict=True)
    presentation_complete: bool = False
    presentation_requested: bool = False
    presentation_summary: str | None = None
    presentation_warnings: list[str] = Field(default_factory=list)
    source_item_id: str | None = None
    analysis_attempted: bool = False
    analysis_complete: bool = False
    analysis_reason: str | None = None
    analysis_start: float | None = None
    analysis_end: float | None = None
    analysis_error: str | None = None
    analysis_choice: Literal["keep_full", "trim", "manual"] | None = None
    analysis_input_tokens: int = 0
    analysis_output_tokens: int = 0
    recording_id: str | None = None
    source_name: str | None = None
    source_duration: float | None = None
    transcription_id: str | None = None
    transcript_revision: int | None = None
    transcription_warning: str | None = None
    title_attempted: bool = False
    title_complete: bool = False
    title_error: str | None = None
    title_input_tokens: int = 0
    title_output_tokens: int = 0
    editorial_quality: EditorialQuality | None = None
    quality: QualityOptions = Field(default_factory=QualityOptions)
    framing: FramingMode | None = None
    split_layout: SplitLayout | None = None
    crop_region: SourceRegion | None = None
    overlays: Overlays = Field(default_factory=list)
    opening_text: str | None = Field(default=None, min_length=1, max_length=120)
    headline_enabled: bool = Field(default=True, strict=True)
    headline_position: Literal["auto", "upper", "centre"] = "auto"
    id: str
    candidate_id: str
    title: str
    start: float
    end: float
    caption_preset: Preset
    caption_customization: CaptionCustomization = Field(default_factory=CaptionCustomization)
    emphasis_words: EmphasisWords = Field(default_factory=list)
    caption_position: Literal["auto", "lower", "centre", "upper"]
    revision: int = 0
    review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    export_id: str | None = None
    pending_export_id: str | None = None
    error: str | None = None
    thumbnail: bool = False
    thumbnail_export_id: str | None = None
    thumbnail_error: str | None = None
    export: Clip | None = None  # Response-only, never stored in the project payload.


class Project(BaseModel):
    stage_seconds: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        default_factory=dict
    )
    transcription_backend: Literal["cpu", "cuda"] | None = None
    analysis_cache_hit: bool = False
    parent_project_id: str | None = None
    avoid_used_clips: bool = False
    selection_exclusions: list[SourceRange] = Field(default_factory=list)
    recording_ids: list[str] = Field(default_factory=list)
    generate_titles: bool = False
    id: str
    recording_id: str | None = None
    youtube_source_id: str | None = None
    source_thumbnail_url: str | None = None
    download_id: str | None = None
    progress: float = Field(default=0, ge=0, le=1)
    title: str
    created_at: str
    settings: ProjectSettings
    status: Literal[
        "queued",
        "downloading",
        "transcribing",
        "analysing",
        "rendering",
        "cancelling",
        "ready",
        "partial",
        "failed",
        "cancelled",
        "interrupted",
    ]
    stage: str = "Waiting for processing"
    error: str | None = None
    transcription_id: str | None = None
    transcript_revision: int | None = None
    transcription_warning: str | None = None
    highlight_id: str | None = None
    analysis_attempted: bool = False
    selection_complete: bool = False
    thumbnail: bool = False
    thumbnail_error: str | None = None
    items: list[ProjectItem] = Field(default_factory=list)


class ItemEdit(BaseModel):
    auto_presentation: bool = Field(default=False, strict=True)
    quality: QualityOptions = Field(default_factory=QualityOptions)
    framing: FramingMode | None = None
    split_layout: SplitLayout | None = None
    crop_region: SourceRegion | None = None
    overlays: Overlays = Field(default_factory=list)
    opening_text: str | None = Field(default=None, min_length=1, max_length=120, strict=True)
    headline_enabled: bool = Field(default=True, strict=True)
    headline_position: Literal["auto", "upper", "centre"] = "auto"
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(ge=0, strict=True)
    review: Literal["unreviewed", "accepted", "rejected"] = "unreviewed"
    title: str = Field(min_length=1, max_length=120, strict=True)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    caption_preset: Preset
    caption_customization: CaptionCustomization = Field(default_factory=CaptionCustomization)
    emphasis_words: EmphasisWords = Field(default_factory=list)
    caption_position: Literal["auto", "lower", "centre", "upper"]
    _title = field_validator("title")(ProjectCreate.title_text.__func__)

    @field_validator("opening_text")
    @classmethod
    def opening(cls, value):
        return HeadlineOptions(text=value).text if value is not None else None

    @model_validator(mode="after")
    def valid_interval(self):
        if any(overlay.end > self.end - self.start for overlay in self.overlays):
            raise ValueError("Overlay timing must remain inside the trimmed clip.")
        kept = retained_intervals(self.start, self.end, self.quality)
        mapped = mapped_overlays(self.overlays, self.start, kept)
        if len(mapped) != len(self.overlays):
            raise ValueError(
                "An edit removes a text overlay entirely or leaves it too short. Adjust the overlay or restore that interval."
            )
        if self.quality.smart_framing and (self.framing == "split" or self.crop_region):
            raise ValueError("Smart framing cannot be combined with manual crop regions.")
        validate_framing(self.framing, self.split_layout, self.crop_region)
        if self.framing == "follow" and self.quality.smart_framing:
            raise ValueError(
                "Auto follow creates its own tracking plan. Clear the staged smart framing first."
            )
        if not 0.1 <= self.end - self.start <= MAX_MEDIA_SECONDS:
            raise ValueError("Exports must be between 0.1 seconds and six hours long.")
        layout(
            mapped,
            sum(b - a for a, b in kept),
            HeadlineOptions(text=self.opening_text or self.title, position=self.headline_position)
            if self.headline_enabled
            else None,
            self.caption_position if self.caption_preset != "none" else None,
            split=self.framing == "split",
        )
        return self


class MoreClipsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    max_clips: int = Field(default=3, ge=1, le=20, strict=True)
    remote_consent: Literal[True]

    @field_validator("remote_consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Approve transcript sharing and another AI analysis first.")
        return value


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry_analysis: bool = Field(default=False, strict=True)
    retry_titles: bool = Field(default=False, strict=True)


class PostedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    posted: bool = Field(strict=True)
