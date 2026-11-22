import hashlib
import json
import math
import re
from dataclasses import dataclass
from uuid import uuid4

from .highlight_models import (
    OVERLAP_SECONDS,
    WINDOW_SECONDS,
    Candidate,
    HighlightError,
    HighlightRequest,
    ProposedHighlights,
)
from .transcription_models import TranscriptWord


@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str


def build_segments(words: list[TranscriptWord], duration: float) -> list[Segment]:
    segments: list[Segment] = []
    group: list[TranscriptWord] = []
    previous_start = -1.0
    previous_id = -1

    def flush() -> None:
        if group:
            segments.append(
                Segment(
                    group[0].id,
                    group[0].start,
                    max(w.end for w in group),
                    " ".join(w.text for w in group),
                )
            )
            group.clear()

    for word in words:
        if word.id <= previous_id or word.start < previous_start or word.end > duration + 0.05:
            raise HighlightError("Saved transcript contains invalid word ordering or timing.")
        previous_id, previous_start = word.id, word.start
        if not word.text:
            continue
        if group and (word.start - group[-1].end > 1.5 or word.end - group[0].start > 12):
            flush()
        group.append(word)
        if re.search(r"[.!?][\"')\]]?$", word.text) and word.end - group[0].start >= 1:
            flush()
    flush()
    return segments


def build_windows(
    segments: list[Segment], maximum: int, *, window_seconds: float = WINDOW_SECONDS
) -> list[list[Segment]]:
    if not math.isfinite(window_seconds) or window_seconds <= OVERLAP_SECONDS:
        raise HighlightError("Highlight windows must be finite and longer than their overlap.")
    if not segments:
        return []
    windows: list[list[Segment]] = []
    start_index = 0
    while start_index < len(segments):
        start = segments[start_index].start
        end_index = start_index
        while end_index < len(segments) and segments[end_index].start < start + window_seconds:
            end_index += 1
        windows.append(segments[start_index:end_index])
        if len(windows) > maximum:
            raise HighlightError(
                "This transcript exceeds the configured highlight request budget. Increase CLIPPING_HIGHLIGHT_MAX_WINDOWS deliberately before retrying."
            )
        if end_index == len(segments):
            break
        next_index = start_index + 1
        while (
            next_index < end_index
            and segments[next_index].start < start + window_seconds - OVERLAP_SECONDS
        ):
            next_index += 1
        start_index = next_index
    return windows


def window_text(window: list[Segment]) -> str:
    return json.dumps(
        [
            {
                "id": segment.id,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text,
            }
            for segment in window
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def ground_candidates(
    proposed: ProposedHighlights,
    window: list[Segment],
    request: HighlightRequest,
    duration: float,
    job_id: str,
) -> tuple[list[Candidate], int]:
    positions = {segment.id: index for index, segment in enumerate(window)}
    result: list[Candidate] = []
    rejected = 0
    for proposal in proposed.candidates:
        first, last = positions.get(proposal.first_segment), positions.get(proposal.last_segment)
        if first is None or last is None or last < first:
            rejected += 1
            continue
        selected = window[first : last + 1]
        start, end = selected[0].start, min(max(s.end for s in selected), duration)
        text = " ".join(s.text for s in selected)
        if request.quality_mode:
            assessment = proposal.quality
            if (
                assessment is None
                or min(
                    assessment.hook, assessment.context, assessment.payoff, assessment.standalone
                )
                < 3
                or assessment.opening_evidence not in selected[0].text
                or assessment.ending_evidence not in selected[-1].text
            ):
                rejected += 1
                continue
            # Bounded room around whole source segments, without adding adjacent speech.
            before = max(request.analysis_start, window[first - 1].end if first > 0 else 0)
            after = min(
                duration,
                request.analysis_end if request.analysis_end is not None else duration,
                window[last + 1].start if last + 1 < len(window) else duration,
            )
            available = max(0, request.max_seconds - (end - start))
            lead = min(0.12, available, max(0, start - before))
            tail = min(0.18, max(0, available - lead), max(0, after - end))
            start, end = start - lead, end + tail
        # Evidence must be a verbatim passage inside this exact selection.
        if (
            not (
                math.isfinite(start)
                and math.isfinite(end)
                and request.min_seconds <= end - start <= request.max_seconds
                and start >= request.analysis_start
                and end <= (request.analysis_end if request.analysis_end is not None else duration)
            )
            or len(proposal.evidence) < 8
            or proposal.evidence not in text
        ):
            rejected += 1
            continue
        fingerprint = hashlib.sha256(
            json.dumps([start, end, proposal.evidence], ensure_ascii=False).encode()
        ).hexdigest()
        result.append(
            Candidate(
                id=str(uuid4()),
                job_id=job_id,
                start=start,
                end=end,
                score=round(
                    5
                    * (
                        proposal.quality.hook
                        + proposal.quality.context
                        + proposal.quality.payoff
                        + proposal.quality.standalone
                    )
                )
                if request.quality_mode
                else proposal.score,
                quality=proposal.quality,
                title=proposal.title,
                reason=proposal.reason,
                evidence=proposal.evidence,
                category=proposal.category,
                fingerprint=fingerprint,
            )
        )
    return result, rejected


def rank_unique(candidates: list[Candidate], limit: int) -> list[Candidate]:
    selected: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.start, c.end)):
        duplicate = False
        for other in selected:
            intersection = max(0, min(candidate.end, other.end) - max(candidate.start, other.start))
            union = max(candidate.end, other.end) - min(candidate.start, other.start)
            words = set(re.findall(r"\w+", candidate.evidence.casefold()))
            other_words = set(re.findall(r"\w+", other.evidence.casefold()))
            similarity = len(words & other_words) / max(1, len(words | other_words))
            if candidate.fingerprint == other.fingerprint or (
                intersection / union >= 0.65 and similarity >= 0.5
            ):
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
            if len(selected) == limit:
                break
    return selected
