from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.highlight_models import (
    HighlightError,
    HighlightRequest,
    ProposedHighlights,
    SourceRange,
)
from backend.highlight_selection import (
    Segment,
    build_segments,
    build_windows,
    ground_candidates,
    rank_unique,
    unused_windows,
)
from backend.transcription_models import TranscriptWord


def request(**changes):
    return HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=True, **changes)


def proposal(**changes):
    return {
        "first_segment": 0,
        "last_segment": 10,
        "title": "An unexpected lesson",
        "reason": "The setup leads to a clear and useful payoff.",
        "score": 85,
        "category": "insight",
        "evidence": "A useful lesson.",
        **changes,
    }


def test_segments_preserve_source_times_corrections_and_silence():
    words = [
        TranscriptWord(
            id=0, start=100.0, end=101.0, text="Corrected", probability=0.9, edited=True
        ),
        TranscriptWord(id=1, start=101.0, end=102.0, text="sentence.", probability=0.9),
        TranscriptWord(id=2, start=110.0, end=111.0, text="", probability=0.9, edited=True),
        TranscriptWord(id=3, start=115.0, end=117.0, text="Later.", probability=0.9),
    ]
    segments = build_segments(words, 120)
    assert segments == [Segment(0, 100, 102, "Corrected sentence."), Segment(3, 115, 117, "Later.")]
    with pytest.raises(HighlightError):
        build_segments(list(reversed(words)), 120)
    with pytest.raises(HighlightError):
        build_segments(words, 105)


def test_exclusions_split_windows_even_across_a_silent_gap():
    words = [
        TranscriptWord(
            id=i, start=float(i * 10), end=float(i * 10 + 9), text=f"Evidence {i}.", probability=0.9
        )
        for i in range(6)
    ]
    options = request(min_seconds=8, excluded_ranges=[SourceRange(start=19.5, end=20.0)])
    windows = unused_windows(words, 60, options, 2, window_seconds=180)
    assert [[s.id for s in window] for window in windows] == [[0, 1], [2, 3, 4, 5]]
    with pytest.raises(HighlightError):
        unused_windows(words, 60, options, 1, window_seconds=180)


def test_candidate_cannot_bridge_a_used_interval():
    window = [Segment(0, 0.0, 9.0, "A useful lesson."), Segment(1, 20.0, 29.0, "The ending.")]
    grounded, rejected = ground_candidates(
        ProposedHighlights(candidates=[proposal(last_segment=1)]),
        window,
        request(min_seconds=8, excluded_ranges=[SourceRange(start=10.0, end=20.0)]),
        60,
        str(uuid4()),
    )
    assert grounded == [] and rejected == 1


@pytest.mark.parametrize(
    "start,end", [(-1.0, 2.0), (2.0, 2.0), (3.0, 2.0), (float("nan"), 2.0), (1.0, float("inf"))]
)
def test_invalid_excluded_range_is_rejected(start, end):
    with pytest.raises(ValidationError):
        SourceRange(start=start, end=end)


def test_longer_windows_support_multi_minute_candidates_without_losing_source_times():
    segments = [Segment(i, i * 10, i * 10 + 5, f"Sentence {i}.") for i in range(48)]
    windows = build_windows(segments, 1, window_seconds=510)
    assert windows == [segments]
    assert windows[0][-1].end == 475
    for invalid in (float("nan"), float("inf"), 30):
        with pytest.raises(HighlightError):
            build_windows(segments, 1, window_seconds=invalid)


def test_windows_overlap_and_cover_every_segment_without_compressing_gaps():
    segments = [Segment(i, i * 10, i * 10 + 5, f"Sentence {i}.") for i in range(40)]
    windows = build_windows(segments, 3)
    assert {s.id for w in windows for s in w} == set(range(40))
    assert {s.id for s in windows[0]} & {s.id for s in windows[1]} == {15, 16, 17}
    assert [w[0].start for w in windows] == [0, 150, 300]
    with pytest.raises(HighlightError, match="budget"):
        build_windows(segments, 2)
    assert build_windows([], 1) == []
    assert len(build_windows([segments[0], Segment(99, 5000, 5002, "Later.")], 2)) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"first_segment": 99},
        {"last_segment": 0, "first_segment": 10},
        {"evidence": "A completely invented quote."},
        {"evidence": "short"},
    ],
)
def test_ungrounded_candidates_are_rejected(changes):
    window = [Segment(0, 2, 8, "The real setup."), Segment(10, 18, 25, "A useful lesson.")]
    result, rejected = ground_candidates(
        ProposedHighlights(candidates=[proposal(**changes)]), window, request(), 30, "job"
    )
    assert result == []
    assert rejected == 1


def test_candidate_times_come_only_from_actual_segments():
    window = [Segment(0, 102, 108, "The real setup."), Segment(10, 118, 125, "A useful lesson.")]
    result, rejected = ground_candidates(
        ProposedHighlights(candidates=[proposal()]), window, request(), 130, "job"
    )
    assert rejected == 0
    assert (result[0].start, result[0].end) == (102, 125)
    assert result[0].evidence in " ".join(s.text for s in window)
    assert (
        ground_candidates(
            ProposedHighlights(candidates=[proposal()]), window, request(min_seconds=30), 130, "job"
        )[1]
        == 1
    )
    assert ground_candidates(ProposedHighlights(candidates=[]), window, request(), 130, "job") == (
        [],
        0,
    )


def test_rank_deduplicates_same_beat_but_preserves_distinct_overlapping_evidence():
    window = [
        Segment(0, 0, 8, "A real setup with multiple possible outcomes."),
        Segment(10, 8, 25, "A useful lesson. Another unexpected outcome."),
    ]
    candidates, _ = ground_candidates(
        ProposedHighlights(
            candidates=[
                proposal(score=70),
                proposal(score=90),
                proposal(first_segment=10, evidence="Another unexpected outcome.", score=80),
            ]
        ),
        window,
        request(),
        30,
        "job",
    )
    result = rank_unique(candidates, 10)
    assert [c.score for c in result] == [90, 80]
    assert len(rank_unique(candidates, 1)) == 1


@pytest.mark.parametrize(
    "changes",
    [{"score": 101}, {"score": True}, {"title": " "}, {"reason": "bad\x00text"}, {"start": 1}],
)
def test_model_output_schema_is_strict(changes):
    with pytest.raises(ValidationError):
        ProposedHighlights(candidates=[proposal(**changes)])


def test_request_rejects_impossible_durations_and_requires_remote_consent():
    with pytest.raises(ValidationError):
        request(min_seconds=60, max_seconds=15)
    with pytest.raises(ValidationError):
        HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=False)


@pytest.mark.parametrize("consent", [1, "true", "yes", None])
def test_remote_consent_requires_an_explicit_boolean(consent):
    with pytest.raises(ValidationError):
        HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=consent)
