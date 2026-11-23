from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ai_instructions import AIInstructions
from .limits import MAX_MEDIA_SECONDS
from .transcription_models import TranscriptionJob, clean_text

ACTIVE = {"queued", "analysing", "cancelling"}
PROMPT_VERSION = "highlights-v10-context"
DISCOVERY_VERSION = "windowed-discovery-v3-context"
WINDOW_SECONDS = 180
OVERLAP_SECONDS = 30
MAX_COVERAGE_WINDOW_SECONDS = 600
ContentProfile = Literal[
    "auto", "comedy", "podcast", "gaming", "interview", "education", "entertainment"
]


class HighlightError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class HighlightCancelled(Exception):
    pass


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    start: float = Field(ge=0, le=MAX_MEDIA_SECONDS, strict=True)
    end: float = Field(gt=0, le=MAX_MEDIA_SECONDS, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Source range end must be after its start.")
        return self


class HighlightRequest(BaseModel):
    excluded_ranges: list[SourceRange] = Field(default_factory=list, max_length=5000)
    ai_instructions: AIInstructions = ""
    quality_mode: bool = Field(default=False, strict=True)
    content_profile: ContentProfile = "auto"
    multimodal: bool = Field(default=False, strict=True)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    transcription_id: UUID
    revision: int = Field(ge=1, strict=True)
    remote_consent: Literal[True]
    min_seconds: int = Field(default=15, ge=8, le=MAX_MEDIA_SECONDS, strict=True)
    max_seconds: int = Field(default=60, ge=8, le=MAX_MEDIA_SECONDS, strict=True)
    max_candidates: int = Field(default=10, ge=1, le=20, strict=True)
    regenerate: bool = Field(default=False, strict=True)
    duration_mode: Literal["auto", "manual"] = "manual"
    analysis_start: float = Field(default=0, ge=0, strict=True)
    analysis_end: float | None = Field(default=None, gt=0, strict=True)

    @field_validator("remote_consent", mode="before")
    @classmethod
    def explicit_consent(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("Explicit transcript-sharing consent is required.")
        return True

    @model_validator(mode="after")
    def valid_duration(self) -> "HighlightRequest":
        if self.min_seconds > self.max_seconds:
            raise ValueError("Minimum duration cannot exceed maximum duration.")
        if self.analysis_end is not None and self.analysis_end <= self.analysis_start:
            raise ValueError("Analysis end must be after analysis start.")
        return self


class EditorialQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook: int = Field(ge=0, le=5, strict=True)
    context: int = Field(ge=0, le=5, strict=True)
    payoff: int = Field(ge=0, le=5, strict=True)
    standalone: int = Field(ge=0, le=5, strict=True)
    opening_evidence: str = Field(min_length=1, max_length=120, strict=True)
    ending_evidence: str = Field(min_length=1, max_length=120, strict=True)

    @property
    def score(self) -> int:
        return 5 * (self.hook + self.context + self.payoff + self.standalone)


class ProposedCandidate(BaseModel):
    quality: EditorialQuality | None = None
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    first_segment: int = Field(ge=0, strict=True)
    last_segment: int = Field(ge=0, strict=True)
    score: int = Field(ge=0, le=100, strict=True)
    title: str = Field(min_length=1, max_length=120, strict=True)
    reason: str = Field(min_length=1, max_length=500, strict=True)
    evidence: str = Field(min_length=1, max_length=320, strict=True)
    category: Literal["insight", "story", "humour", "reaction", "question", "other"]

    @field_validator("title", "reason")
    @classmethod
    def safe_text(cls, value: str) -> str:
        result = clean_text(value)
        if not result:
            raise ValueError("Candidate text cannot be blank.")
        return result

    @field_validator("evidence")
    @classmethod
    def exact_evidence(cls, value: str) -> str:
        if not clean_text(value):
            raise ValueError("Candidate evidence cannot be blank.")
        return value


class ProposedHighlights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[ProposedCandidate] = Field(max_length=5)


class NarrativeNote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    first_segment: int = Field(ge=0)
    last_segment: int = Field(ge=0)
    role: Literal["topic", "setup", "question_answer", "turning_point", "payoff", "callback"]
    summary: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=8, max_length=120)


class DiscoveryProposal(ProposedHighlights):
    narrative: list[NarrativeNote] = Field(default_factory=list, max_length=3)


Review = Literal["unreviewed", "accepted", "rejected"]


class VisualEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    frame_times: list[float] = Field(min_length=2, max_length=3)
    description: str = Field(min_length=1, max_length=300)


class Candidate(BaseModel):
    quality: EditorialQuality | None = None
    visual: VisualEvidence | None = None
    id: str
    job_id: str
    start: float
    end: float
    score: int
    title: str
    reason: str
    evidence: str
    category: str
    fingerprint: str
    review: Review = "unreviewed"
    revision: int = 0


class HighlightJob(BaseModel):
    media_status: Literal["sampling", "reviewing", "ready", "unavailable"] | None = None
    media_warning: str | None = None
    elapsed_seconds: float | None = None
    vision_model: str | None = None
    vision_input_tokens: int = 0
    vision_output_tokens: int = 0
    discovery_cache_hit: bool = False
    editorial_status: Literal["pending", "reviewing", "completed"] | None = None
    discovered_candidates: int = 0
    editorial_options: int = 0
    discovery_duplicates: int = 0
    discovery_rejections: dict[str, int] = Field(default_factory=dict)
    editorial_proposals: int = 0
    editorial_duplicates: int = 0
    editorial_rejections: dict[str, int] = Field(default_factory=dict)
    discovery_shortlist: list[SourceRange] = Field(default_factory=list, max_length=24)
    pool_key: str | None = None
    cache_hit: bool = False
    id: str
    recording_id: str
    transcription_id: str
    transcript_revision: int
    model: str
    status: Literal["queued", "analysing", "cancelling", "succeeded", "failed", "cancelled"]
    total_windows: int
    completed_windows: int = 0
    requests_made: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rejected_candidates: int = 0
    error: str | None = None
    created_at: str
    stale: bool = False
    candidates: list[Candidate] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review: Review
    revision: int = Field(ge=0, strict=True)


class HighlightState(BaseModel):
    latest: HighlightJob | None
    result: HighlightJob | None
    transcription: TranscriptionJob | None


@dataclass(frozen=True)
class ProviderResult:
    candidates: ProposedHighlights
    input_tokens: int
    output_tokens: int
