"""Bounded extractive chapter context and editorial profiles."""

import re
from collections import Counter
from dataclasses import dataclass

from .highlight_models import ContentProfile, EditorialQuality, NarrativeNote
from .highlight_selection import Segment
from .transcription_models import clean_text


@dataclass(frozen=True)
class Profile:
    guidance: str
    weights: tuple[int, int, int, int]


PROFILES: dict[ContentProfile, Profile] = {
    "auto": Profile(
        "Infer the genre from source evidence and adapt the evaluation to each moment.",
        (1, 1, 1, 1),
    ),
    "comedy": Profile(
        "Preserve the setup, misdirection, payoff and meaningful pauses. Look for the reaction only if it completes the joke. A quick exchange often fits 12-45 seconds.",
        (2, 3, 4, 1),
    ),
    "podcast": Profile(
        "Prioritize a specific insight, memorable exchange or opinion that earns its conclusion. A developed explanation may need 25-90 seconds.",
        (2, 3, 2, 3),
    ),
    "gaming": Profile(
        "Look for a goal, a change in the game state and its outcome. Corroborate visual claims with sampled evidence. Short action sequences often fit 12-45 seconds.",
        (3, 2, 3, 2),
    ),
    "interview": Profile(
        "Keep the question when necessary, the substantive answer and any clarification that changes its meaning. Allow 25-90 seconds for a complete exchange.",
        (1, 3, 3, 3),
    ),
    "education": Profile(
        "Keep the problem, explanation or demonstration and a useful takeaway. A self-contained lesson may need 30-90 seconds.",
        (1, 3, 2, 4),
    ),
    "entertainment": Profile(
        "Prefer a specific opening, visible or spoken progression, a turn and an independently entertaining ending. Consider 15-60 seconds.",
        (3, 2, 3, 2),
    ),
}

_STOP = set(
    "about after again because before could going gonna really right should there these thing think "
    "those through today until wanna we're where which while would you're".split()
)


def profile_score(quality: EditorialQuality, profile: ContentProfile) -> int:
    scores = (quality.hook, quality.context, quality.payoff, quality.standalone)
    weights = PROFILES[profile].weights
    return round(20 * sum(s * w for s, w in zip(scores, weights, strict=True)) / sum(weights))


@dataclass(frozen=True)
class Chapter:
    first: Segment
    last: Segment
    terms: tuple[str, ...]
    question: Segment | None

    def payload(self) -> dict:
        return {
            "segment_ids": [self.first.id, self.last.id],
            "range": [round(self.first.start, 3), round(self.last.end, 3)],
            "terms": self.terms,
            "opening_excerpt": self.first.text[:70],
            "closing_excerpt": self.last.text[-70:],
            "question_segment": self.question.id if self.question else None,
        }


def build_context(windows: list[list[Segment]]) -> list[Chapter]:
    segments = sorted({s.id: s for window in windows for s in window}.values(), key=lambda s: s.id)
    groups: list[list[Segment]] = []
    for segment in segments:
        if (
            not groups
            or segment.start - groups[-1][0].start >= 90
            or segment.start - groups[-1][-1].end >= 12
        ):
            groups.append([])
        groups[-1].append(segment)
    chapters = []
    for group in groups:
        terms = Counter(
            word
            for segment in group
            for word in re.findall(r"[^\W\d_]{5,32}", segment.text.casefold())
            if word not in _STOP
        )
        chapters.append(
            Chapter(
                group[0],
                group[-1],
                tuple(word for word, _ in terms.most_common(5)),
                next((s for s in reversed(group) if s.text.rstrip("\"')]").endswith("?")), None),
            )
        )
    return chapters


def nearby_context(chapters: list[Chapter], start: float, end: float) -> dict:
    def distance(chapter: Chapter) -> float:
        return max(0.0, start - chapter.last.end, chapter.first.start - end)

    nearby = sorted(chapters, key=distance)[:3]
    terms = {term for chapter in nearby for term in chapter.terms}
    related = sorted(
        (chapter for chapter in chapters if distance(chapter) >= 30),
        key=lambda chapter: (-len(terms.intersection(chapter.terms)), distance(chapter)),
    )
    return {
        "nearest_chapters": [
            chapter.payload() for chapter in sorted(nearby, key=lambda c: c.first.start)
        ],
        "possible_callbacks": [
            chapter.payload()
            for chapter in related[:2]
            if len(terms.intersection(chapter.terms)) >= 2
        ],
    }


def ground_narrative(notes: list[NarrativeNote], window: list[Segment]) -> list[dict]:
    """Resolve note intervals from source IDs and require a verbatim supporting passage."""
    positions = {segment.id: index for index, segment in enumerate(window)}
    grounded = []
    for note in notes:
        first, last = positions.get(note.first_segment), positions.get(note.last_segment)
        summary = clean_text(note.summary)
        if first is None or last is None or last < first or not summary:
            continue
        selected = window[first : last + 1]
        if note.quote not in " ".join(segment.text for segment in selected):
            continue
        grounded.append(
            {
                "kind": "narrative_note",
                "segment_ids": [note.first_segment, note.last_segment],
                "start": selected[0].start,
                "end": max(segment.end for segment in selected),
                "role": note.role,
                "summary": summary,
                "exact_quote": note.quote,
            }
        )
    return grounded
