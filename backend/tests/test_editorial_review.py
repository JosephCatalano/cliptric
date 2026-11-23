"""Two-stage source, budget, cache and usage checks with simulated provider transport."""

import asyncio
import json
import threading
from uuid import uuid4

import httpx2
import pytest

from backend.editorial_review import MAX_OPTIONS, EditorialProposal, EditorialReviewer, shortlist
from backend.highlight_models import (
    HighlightRequest,
    ProposedHighlights,
    ProviderResult,
    SourceRange,
)
from backend.highlight_selection import Segment, ground_candidates, rank_unique
from backend.tests.test_highlights import finished, request
from backend.tests.test_highlights import service as service
from backend.tests.test_highlights import settings as settings
from backend.tests.test_highlights import tools as tools


def lead(first=0, last=4, **changes):
    return {
        "first_segment": first,
        "last_segment": last,
        "score": 77,
        "category": "story",
        "title": "A clearer complete story",
        "reason": "The ending resolves the opening.",
        "evidence": f"Evidence {first}.",
        **changes,
    }


@pytest.mark.parametrize("outcome", ["valid", "invalid", "empty", "malformed", "api_failure"])
def test_review_meters_one_request_and_never_publishes_raw_leads(service, outcome):
    manager, recording, transcript, provider = service
    calls = []

    def respond(http_request):
        body = json.loads(http_request.content)
        calls.append(body)
        if outcome == "api_failure" and len(calls) == 1:
            return httpx2.Response(503, text="synthetic private response")
        candidate = lead(
            last=6,
            option=1 if outcome == "invalid" else 0,
            quality={
                "hook": 4,
                "context": 4,
                "payoff": 5,
                "standalone": 4,
                "opening_evidence": "Evidence 0.",
                "ending_evidence": "Evidence 6.",
            },
        )
        return httpx2.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "invalid JSON"
                                if outcome == "malformed"
                                else json.dumps(
                                    {
                                        "candidates": [] if outcome == "empty" else [candidate],
                                    }
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    manager.reviewer = EditorialReviewer(manager.settings, transport=httpx2.MockTransport(respond))
    payload = request(transcript, quality_mode=True, max_candidates=1)
    result = finished(manager, manager.submit(recording.id, payload).id)
    assert result.requests_made == 2 and provider.calls == len(calls) == 1
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["store"] is False
    assert "Evidence 6." in calls[0]["input"]  # Beyond the discovery interval.
    assert result.editorial_options == 1
    assert result.discovery_shortlist == [SourceRange(start=0, end=24)]
    if outcome not in {"malformed", "api_failure"}:
        assert (result.input_tokens, result.output_tokens) == (300, 150)
    if outcome == "valid":
        assert result.status == "succeeded" and result.editorial_status == "completed"
        assert len(result.candidates) == 1
        assert result.candidates[0].end == pytest.approx(34.18)
        assert result.candidates[0].score == 85
        assert result.editorial_proposals == 1 and result.editorial_rejections == {}
        assert manager.submit(recording.id, payload).id == result.id
    else:
        assert result.candidates == []
        assert result.status == ("succeeded" if outcome == "empty" else "failed")
        assert manager.results.pool(result.pool_key) == []
        if outcome == "invalid":
            assert result.editorial_proposals == 1
            assert result.editorial_rejections == {"unknown_option": 1}
        if outcome == "api_failure":
            assert result.error.startswith("Editorial review failed.")
            assert "synthetic private response" not in result.error
            manager.results.initialize()
            manager.results.recover()
            retried = finished(manager, manager.submit(recording.id, payload).id)
            assert retried.status == "succeeded" and retried.discovery_cache_hit
            assert retried.requests_made == 1 and provider.calls == 1
            assert (retried.input_tokens, retried.output_tokens) == (100, 50)
            assert len(calls) == 2


def test_cancel_review_preserves_discovery_for_explicit_retry(service, monkeypatch):
    manager, recording, transcript, provider = service
    entered = threading.Event()
    review = manager.reviewer.review
    cancelled = False

    async def hold(*args):
        nonlocal cancelled
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled = True
            raise

    monkeypatch.setattr(manager.reviewer, "review", hold)
    payload = request(transcript)
    job = manager.submit(recording.id, payload)
    assert entered.wait(3)
    assert manager.results.job(job.id).editorial_status == "reviewing"
    manager.cancel(job.id)
    stopped = finished(manager, job.id)
    assert stopped.status == "cancelled" and cancelled and not stopped.candidates
    monkeypatch.setattr(manager.reviewer, "review", review)
    retried = finished(manager, manager.submit(recording.id, payload).id)
    assert retried.status == "succeeded" and retried.discovery_cache_hit
    assert retried.requests_made == 1 and provider.calls == 1


def test_overlapping_windows_send_a_unique_shortlist_and_review_can_drop_leads(
    service, monkeypatch
):
    manager, recording, transcript, provider = service
    segments = [Segment(i, i * 5, i * 5 + 4, f"Evidence {i}.") for i in range(16)]
    monkeypatch.setattr(
        "backend.highlights.unused_windows",
        lambda *args, **kwargs: [
            segments[:10],
            segments[3:],
        ],
    )
    windows = []

    async def discover(window, options):
        windows.append(window)
        first = window[0].id
        proposals = (
            [lead(first=4, last=7), lead(first=0, last=0)]
            if first == 0
            else [
                lead(first=3, last=7, evidence="Evidence 4.", score=60),
                lead(first=4, last=7, evidence="Evidence 7.", score=65),
                lead(first=10, last=13, score=90),
            ]
        )
        return ProviderResult(ProposedHighlights(candidates=proposals), 20, 10)

    async def review(payload, options, limit):
        assert len(windows) == 2
        data = json.loads(payload)
        assert [option["lead_start"] for option in data] == [50, 20]
        return (
            EditorialProposal(
                candidates=[
                    lead(first=4, last=8, option=1, title="Selected after global review"),
                ]
            ),
            10,
            5,
        )

    monkeypatch.setattr(provider, "suggest", discover)
    monkeypatch.setattr(manager.reviewer, "review", review)
    job = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert job.status == "succeeded" and job.requests_made == 3
    assert job.discovered_candidates == 4 and job.editorial_options == 2
    assert job.discovery_duplicates == 2 and job.discovery_rejections == {"duration": 1}
    assert [(c.start, c.end, c.title) for c in job.candidates] == [
        (20, 44, "Selected after global review"),
    ]


def test_shortlist_reserves_review_space_across_the_recording():
    starts = list(range(0, MAX_OPTIONS * 9, 9)) + [1_200, 2_400]
    segments = [Segment(i, start, start + 8, f"Evidence {i}.") for i, start in enumerate(starts)]
    payload = HighlightRequest(
        transcription_id=uuid4(), revision=1, remote_consent=True, min_seconds=8
    )
    candidates = [
        ground_candidates(
            ProposedHighlights(candidates=[lead(i, i, score=100 - i)]),
            segments,
            payload,
            3_000,
            "job",
        )[0][0]
        for i in range(len(segments))
    ]
    options, _ = shortlist(candidates, [segments], payload)
    assert len(options) == MAX_OPTIONS
    assert [option.lead.start for option in options[:3]] == [0, 1_200, 2_400]


def test_shortlist_sees_adjacent_window_but_never_crosses_exclusion():
    segments = [Segment(i, 5.0 * i, 5.0 * i + 4, f"Evidence {i}.") for i in range(8)]
    discover = HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=True)
    candidates = ground_candidates(
        ProposedHighlights(candidates=[lead()]),
        segments[:5],
        discover,
        40,
        "job",
    )[0]
    windows = [segments[:5], segments[3:]]
    options, _ = shortlist(candidates, windows, discover)
    assert [s.id for s in options[0].segments] == list(range(8))
    blocked = discover.model_copy(update={"excluded_ranges": [SourceRange(start=24, end=25)]})
    options, _ = shortlist(candidates, windows, blocked)
    assert [s.id for s in options[0].segments] == list(range(5))


def test_nested_ranges_with_same_evidence_keep_stronger_version():
    segments = [Segment(i, 5.0 * i, 5.0 * i + 4, f"Evidence {i}.") for i in range(12)]
    payload = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        max_seconds=60,
    )
    candidates, invalid = ground_candidates(
        ProposedHighlights(
            candidates=[
                lead(last=11, score=88),
                lead(last=4, score=77),
                lead(first=5, last=8, score=79),
            ]
        ),
        segments,
        payload,
        60,
        "job",
    )
    assert not invalid
    assert [(c.start, c.end) for c in rank_unique(candidates, 10)] == [(0, 59), (25, 44)]


def test_grounding_rejects_out_of_source_timing_and_normalized_fake_evidence():
    segments = [Segment(0, 0, 8, "Evidence 0."), Segment(1, 12, 20.02, "Evidence 1.")]
    payload = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        min_seconds=8,
    )
    candidates, rejected = ground_candidates(
        ProposedHighlights(candidates=[lead(last=1), lead(last=0, evidence="Evidence  0.")]),
        segments,
        payload,
        20,
        "job",
    )
    assert not candidates and rejected == 2
