"""Reviewable hook cuts grounded in saved AI evidence and complete source segments."""

from pydantic import BaseModel

from .highlight_selection import build_segments
from .transcription_models import TranscriptWord


class HookSuggestion(BaseModel):
    start: float
    opening_text: str
    evidence: str
    context: str
    reason: str
    context_start: float
    context_end: float


def suggest_hooks(words: list[dict], candidate, start: float, end: float) -> list[HookSuggestion]:
    segments = build_segments(
        [
            TranscriptWord.model_validate(
                {key: word[key] for key in TranscriptWord.model_fields if key in word}
            )
            for word in words
            if start <= word["start"] and word["end"] <= end
        ],
        end,
    )
    segments = [segment for segment in segments if start <= segment.start and segment.end <= end]
    text = " ".join(segment.text for segment in segments)
    # Never offer stale or unmatched evidence.
    if not candidate or candidate.evidence not in text:
        return []
    result = []
    offset = 0
    evidence_offset = text.index(candidate.evidence)
    for index, segment in enumerate(segments):
        if offset <= evidence_offset < offset + len(segment.text):
            if end - segment.start >= 0.1:
                result.append(
                    HookSuggestion(
                        start=segment.start,
                        opening_text=candidate.evidence[:120],
                        evidence=candidate.evidence,
                        context=" ".join(s.text for s in segments[max(0, index - 1) : index + 2]),
                        context_start=segments[max(0, index - 1)].start,
                        context_end=segments[min(len(segments) - 1, index + 1)].end,
                        reason="Start at the complete source segment containing the saved AI-selected evidence. Review the preceding context before accepting.",
                    )
                )
            break
        offset += len(segment.text) + 1
    return result
