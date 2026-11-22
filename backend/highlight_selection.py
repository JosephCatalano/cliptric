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
    EditorialQuality,
    HighlightError,
    HighlightRequest,
    ProposedHighlights,
    SourceRange,
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


def overlaps(start: float, end: float, ranges: list[SourceRange]) -> bool:
    return any(start < previous.end and end > previous.start for previous in ranges)


def merge_ranges(ranges: list[SourceRange]) -> list[SourceRange]:
    merged: list[SourceRange] = []
    for interval in sorted(ranges, key=lambda item: (item.start, item.end)):
        if merged and interval.start <= merged[-1].end:
            merged[-1] = SourceRange(start=merged[-1].start, end=max(merged[-1].end, interval.end))
        else:
            merged.append(interval)
    return merged


def unused_windows(words, duration, request, maximum, *, window_seconds):
    """Keep original word times, splitting windows at every previously used section."""
    blocked = merge_ranges(request.excluded_ranges)
    if not blocked:
        return build_windows(
            build_segments(words, duration), maximum, window_seconds=window_seconds
        )
    windows, group = [], []
    index = 0

    def flush():
        if group and max(word.end for word in group) - group[0].start >= request.min_seconds:
            windows.extend(
                build_windows(
                    build_segments(group, duration),
                    maximum - len(windows),
                    window_seconds=window_seconds,
                )
            )
        group.clear()

    for word in words:
        while index < len(blocked) and word.start >= blocked[index].end:
            flush()
            index += 1
        if index < len(blocked) and word.end > blocked[index].start:
            flush()
        else:
            group.append(word)
    flush()
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


def meets_quality(quality: EditorialQuality | None, first: Segment, last: Segment) -> bool:
    return (
        quality is not None
        and min(quality.hook, quality.context, quality.payoff, quality.standalone) >= 3
        and bool(quality.opening_evidence.strip())
        and bool(quality.ending_evidence.strip())
        and quality.opening_evidence in first.text
        and quality.ending_evidence in last.text
    )


def ground_candidates(
    proposed: ProposedHighlights,
    window: list[Segment],
    request: HighlightRequest,
    duration: float,
    job_id: str,
) -> tuple[list[Candidate], int]:
    if any(
        not (0 <= s.start < s.end <= duration + 0.05)
        or (i > 0 and (s.id <= window[i - 1].id or s.start < window[i - 1].start))
        for i, s in enumerate(window)
    ):
        raise HighlightError("Transcript segments have invalid source timing or order.")
    positions = {segment.id: index for index, segment in enumerate(window)}
    result: list[Candidate] = []
    rejected = 0
    for proposal in proposed.candidates:
        first, last = positions.get(proposal.first_segment), positions.get(proposal.last_segment)
        if first is None or last is None or last < first:
            rejected += 1
            continue
        selected = window[first : last + 1]
        start, end = selected[0].start, max(s.end for s in selected)
        text = " ".join(s.text for s in selected)
        if request.quality_mode:
            if not meets_quality(proposal.quality, selected[0], selected[-1]):
                rejected += 1
                continue
            # Bounded room around whole source segments, without adding adjacent speech.
            before = max(request.analysis_start, window[first - 1].end if first > 0 else 0)
            after = min(
                duration,
                request.analysis_end if request.analysis_end is not None else duration,
                window[last + 1].start if last + 1 < len(window) else duration,
            )
            for blocked in request.excluded_ranges:
                if blocked.end <= start:
                    before = max(before, blocked.end)
                if blocked.start >= end:
                    after = min(after, blocked.start)
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
                and end
                <= min(
                    duration, request.analysis_end if request.analysis_end is not None else duration
                )
            )
            or len(proposal.evidence) < 8
            or proposal.evidence not in text
            or overlaps(start, end, request.excluded_ranges)
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
                score=proposal.quality.score
                if request.quality_mode and proposal.quality
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


def rank_unique(
    candidates: list[Candidate],
    limit: int,
    *,
    iou_threshold: float = 0.65,
    evidence_threshold: float = 0.5,
) -> list[Candidate]:
    """Rank one source at a time. Require shared evidence for fuzzy time matches."""
    selected: list[tuple[Candidate, set[str]]] = []
    for candidate in sorted(candidates, key=lambda c: (-c.score, c.start, -c.end)):
        words = set(re.findall(r"\w+", candidate.evidence.casefold()))
        duplicate = False
        for other, other_words in selected:
            intersection = max(0, min(candidate.end, other.end) - max(candidate.start, other.start))
            union = max(candidate.end, other.end) - min(candidate.start, other.start)
            if not intersection:
                continue
            similarity = len(words & other_words) / max(1, len(words | other_words))
            if (
                candidate.fingerprint == other.fingerprint
                or (intersection / union >= iou_threshold and similarity >= evidence_threshold)
                or (
                    intersection == min(candidate.end - candidate.start, other.end - other.start)
                    and candidate.evidence == other.evidence
                )
            ):
                duplicate = True
                break
        if not duplicate:
            selected.append((candidate, words))
            if len(selected) == limit:
                break
    return [candidate for candidate, _ in selected]
