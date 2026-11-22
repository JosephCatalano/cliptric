import math
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TranscriptionStatus = Literal[
    "queued", "preparing", "transcribing", "cancelling", "succeeded", "failed", "cancelled"
]
ACTIVE_STATUSES = {"queued", "preparing", "transcribing", "cancelling"}
MAX_WORDS = 120_000
MAX_RESULT_BYTES = 32 * 1024**2


class TranscriptionError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class TranscriptionCancelled(Exception):
    pass


class TranscriptionJob(BaseModel):
    id: str
    recording_id: str
    status: TranscriptionStatus
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    model: str
    language: str
    error: str | None
    word_count: int
    revision: int
    created_at: str
    warning: str | None = None
    device: Literal["cpu", "cuda"] | None = None


class InferenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: Literal["cpu", "cuda"]
    compute_type: Literal["int8", "int8_float16"]
    warning: str | None = Field(default=None, max_length=500)


def clean_text(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") and not char.isspace() for char in value):
        raise ValueError("Caption text cannot contain control characters.")
    return " ".join(value.split())


class TranscriptWord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(ge=0, lt=MAX_WORDS, strict=True)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    text: str = Field(max_length=48, strict=True)
    probability: float = Field(ge=0, le=1, strict=True)
    edited: bool = False

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return clean_text(value)

    @model_validator(mode="after")
    def valid_interval(self) -> "TranscriptWord":
        if self.end <= self.start:
            raise ValueError("Word end must be later than its start.")
        return self


class EditableWord(TranscriptWord):
    original_start: float
    original_end: float
    timing_edited: bool = False


class WordsPage(BaseModel):
    id: str
    revision: int
    words: list[EditableWord]


class WordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(ge=0, lt=MAX_WORDS, strict=True)
    text: str = Field(max_length=48, strict=True)
    start: float | None = Field(default=None, ge=0, strict=True)
    end: float | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def valid_timing(self) -> "WordUpdate":
        if (self.start is None) != (self.end is None):
            raise ValueError("Supply both word start and end when correcting timing.")
        if self.start is not None and self.end <= self.start:
            raise ValueError("Word end must be later than its start.")
        return self

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return clean_text(value)


class WordsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, strict=True)
    updates: list[WordUpdate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_words(self) -> "WordsPatch":
        if len({word.id for word in self.updates}) != len(self.updates):
            raise ValueError("Each word may be updated only once per request.")
        return self


class RevisionResponse(BaseModel):
    id: str
    revision: int


def validate_window(start: float, end: float, duration: float, maximum: float) -> None:
    if (
        not all(math.isfinite(value) for value in (start, end))
        or start < 0
        or end <= start
        or end - start > maximum + 0.001
        or end > duration + 0.05
    ):
        raise TranscriptionError(
            "Select a valid interval within the recording, at most 120 seconds."
        )
