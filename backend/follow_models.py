"""Server-generated, immutable single-person framing paths."""

from typing import Literal

from pydantic import Field, model_validator

from .editing import FramingPoint, SmartFraming

FOLLOW_VERSION = 1
MAX_FOLLOW_SECONDS = 600


class FollowPlan(SmartFraming):
    version: Literal[1] = FOLLOW_VERSION
    detector: Literal["faces"] = "faces"
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    points: list[FramingPoint] = Field(min_length=1, max_length=1202)

    @model_validator(mode="after")
    def interval(self):
        if self.end <= self.start or any(not self.start <= p.at <= self.end for p in self.points):
            raise ValueError("Auto follow points must remain inside their source interval.")
        if self.points[0].at != self.start:
            raise ValueError("Auto follow must describe the opening frame.")
        return self


def follow_fit(start: float, end: float, reason: str) -> FollowPlan:
    return FollowPlan(
        start=start,
        end=end,
        strategy="fit",
        width=1.0,
        height=1.0,
        points=[FramingPoint(at=start, x=0.0, y=0.0)],
        warnings=[reason],
    )
