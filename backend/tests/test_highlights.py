"""Synthetic transcript/provider tests. These do not establish AI highlight quality."""

import asyncio
import json
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.highlights as highlight_module
from backend.highlight_models import (
    HighlightError,
    HighlightJob,
    HighlightRequest,
    ProposedHighlights,
    ProviderResult,
    ReviewRequest,
)
from backend.highlights import Highlights, create_highlight_router
from backend.models import Recording
from backend.storage import Store, now
from backend.tests.editorial_fixtures import SyntheticEditorialReviewer
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.transcription_models import WordsPatch
from backend.transcription_store import TranscriptStore


class SyntheticProvider:
    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.hold = False
        self.empty = False
        self.error = None
        self.cancelled = False

    async def suggest(self, window, request, *, context=None):
        self.calls += 1
        self.entered.set()
        try:
            while self.hold:
                await asyncio.sleep(0.01)
            if self.error:
                raise self.error
            proposals = (
                []
                if self.empty
                else [
                    {
                        "first_segment": window[0].id,
                        "last_segment": window[4].id,
                        "score": 85,
                        "title": "A useful lesson",
                        "category": "insight",
                        "reason": "The passage has a clear setup and payoff.",
                        "evidence": window[0].text,
                    }
                ]
            )
            return ProviderResult(ProposedHighlights(candidates=proposals), 200, 100)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.fixture
def service(settings, tmp_path):
    store = Store(settings.data_dir)
    transcripts = TranscriptStore(store)
    transcripts.initialize()
    recording = Recording(
        id=str(uuid4()),
        name="Synthetic speech.mp4",
        duration=80,
        width=320,
        height=180,
        has_audio=True,
        size_bytes=100,
        created_at=now(),
    )
    store.save_recording(recording, ".mp4")
    transcript = transcripts.create(str(uuid4()), recording.id)
    path = tmp_path / "words.ndjson"
    words = [
        {
            "id": i,
            "start": float(i * 5),
            "end": float(i * 5 + 4),
            "text": f"Evidence {i}.",
            "probability": 0.9,
        }
        for i in range(16)
    ]
    path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
    transcripts.publish(
        transcript.id,
        "synthetic-source",
        "synthetic-settings",
        path,
        recording.duration,
        len(words),
    )
    provider = SyntheticProvider()
    manager = Highlights(
        store,
        transcripts,
        replace(settings, openai_api_key="test-not-a-real-key"),
        provider,
        reviewer=SyntheticEditorialReviewer(),
    )
    manager.initialize()
    yield manager, recording, transcript, provider
    manager.close()


def request(transcript, **changes):
    return HighlightRequest(
        transcription_id=transcript.id, revision=1, remote_consent=True, **changes
    )


def finished(manager, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.results.job(identifier)
        if job.status not in {"queued", "analysing", "cancelling"}:
            # Wait for the worker's terminal cleanup before submitting another job.
            with manager.lock:
                if manager.active is None:
                    return job
        time.sleep(0.01)
    pytest.fail("Synthetic highlight worker did not finish.")


def test_updated_prompt_uses_new_cache_and_preserves_previous_results(service, monkeypatch):
    manager, recording, transcript, provider = service
    current_version = highlight_module.PROMPT_VERSION
    monkeypatch.setattr(highlight_module, "PROMPT_VERSION", "transcript-highlights-v3-quality")
    previous = finished(manager, manager.submit(recording.id, request(transcript)).id)
    manager.results.update(previous.id, editorial_status=None)
    monkeypatch.setattr(highlight_module, "PROMPT_VERSION", current_version)
    current = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert current.id != previous.id and provider.calls == 1 and current.discovery_cache_hit
    assert current.requests_made == 1 and current.editorial_status == "completed"
    assert manager.results.job(previous.id).status == "succeeded"
    assert manager.submit(recording.id, request(transcript)).id == current.id
    assert provider.calls == 1
    assert manager.reviewer.calls == 2


def test_success_cache_review_regeneration_and_persistence(service):
    manager, recording, transcript, provider = service
    job = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert job.status == "succeeded"
    assert (job.requests_made, job.completed_windows, job.input_tokens, job.output_tokens) == (
        2,
        1,
        210,
        105,
    )
    candidate = job.candidates[0]
    assert (candidate.start, candidate.end) == (0, 24)
    reviewed = manager.results.review(candidate.id, ReviewRequest(review="accepted", revision=0))
    assert reviewed.revision == 1
    with pytest.raises(HighlightError, match="another window"):
        manager.results.review(candidate.id, ReviewRequest(review="rejected", revision=0))
    assert manager.submit(recording.id, request(transcript)).id == job.id
    assert provider.calls == 1
    regenerated = finished(
        manager, manager.submit(recording.id, request(transcript, regenerate=True)).id
    )
    assert regenerated.candidates[0].review == "accepted"
    assert provider.calls == 2
    manager.results.initialize()
    manager.results.recover()
    assert manager.state(recording.id).result.id == regenerated.id
    assert manager.results.job(job.id).candidates[0].review == "accepted"
    assert manager.store.draft(recording).revision == 0


def test_saved_corrections_invalidate_results_and_inflight_publication(service):
    manager, recording, transcript, provider = service
    old = finished(manager, manager.submit(recording.id, request(transcript)).id)
    provider.entered.clear()
    provider.hold = True
    pending = manager.submit(recording.id, request(transcript, regenerate=True))
    assert provider.entered.wait(2)
    manager.transcripts.patch(
        transcript.id, WordsPatch(revision=1, updates=[{"id": 0, "text": "Changed evidence."}])
    )
    provider.hold = False
    failed = finished(manager, pending.id)
    assert failed.status == "failed" and "changed" in failed.error
    assert manager.state(recording.id).result.id == old.id
    assert manager.state(recording.id).result.stale
    with pytest.raises(HighlightError, match="transcript changed"):
        manager.results.review(old.candidates[0].id, ReviewRequest(review="accepted", revision=0))
    with pytest.raises(HighlightError, match="current saved revision"):
        manager.submit(recording.id, request(transcript))
    new = HighlightRequest(transcription_id=transcript.id, revision=2, remote_consent=True)
    corrected = finished(manager, manager.submit(recording.id, new).id)
    assert corrected.candidates[0].evidence == "Changed evidence."
    assert not corrected.stale


def test_cancel_interrupts_provider_without_publishing_and_deduplicates_active_requests(service):
    manager, recording, transcript, provider = service
    provider.hold = True
    pending = manager.submit(recording.id, request(transcript))
    assert provider.entered.wait(2)
    assert manager.submit(recording.id, request(transcript)).id == pending.id
    with pytest.raises(HighlightError, match="active"):
        manager.submit(recording.id, request(transcript, max_seconds=45))
    assert manager.cancel(pending.id).status == "cancelling"
    cancelled = finished(manager, pending.id)
    assert cancelled.status == "cancelled"
    assert cancelled.candidates == []
    assert provider.calls == 1 and provider.cancelled
    with pytest.raises(HighlightError):
        manager.cancel(pending.id)


def test_transcript_change_stops_remaining_billable_windows(service):
    manager, recording, transcript, provider = service
    # This verifies stopping future dispatch. Other tests cover already in-flight calls.
    manager.settings = replace(manager.settings, highlight_concurrency=1)
    with manager.store.connection() as db:
        db.execute("UPDATE recordings SET duration=400 WHERE id=?", (recording.id,))
        db.execute(
            "UPDATE transcript_words SET start=start+200,end=end+200 WHERE transcription_id=? AND id>=8",
            (transcript.id,),
        )
    original = provider.suggest

    async def changing(window, payload, *, context=None):
        result = await original(window, payload)
        manager.transcripts.patch(
            transcript.id, WordsPatch(revision=1, updates=[{"id": 0, "text": "Changed evidence."}])
        )
        return result

    provider.suggest = changing
    result = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert result.total_windows == 2
    assert result.status == "failed" and "Further API requests were stopped" in result.error
    assert result.requests_made == 1 and provider.calls == 1
    assert result.candidates == []


@pytest.mark.parametrize("duration_mode", ["auto", "manual"])
def test_discovery_window_is_independent_of_maximum_clip_duration(service, duration_mode):
    manager, recording, transcript, provider = service
    with manager.store.connection() as db:
        db.execute("UPDATE recordings SET duration=3000 WHERE id=?", (recording.id,))
        db.execute(
            "UPDATE transcript_words SET start=start+240,end=end+240 WHERE transcription_id=? AND id>=8",
            (transcript.id,),
        )
    result = finished(
        manager,
        manager.submit(
            recording.id, request(transcript, duration_mode=duration_mode, max_seconds=3000)
        ).id,
    )
    assert result.status == "succeeded"
    assert result.total_windows == provider.calls == 2
    assert result.editorial_options == 2


def test_provider_failure_preserves_previous_results_and_does_not_retry(service):
    manager, recording, transcript, provider = service
    old = finished(manager, manager.submit(recording.id, request(transcript)).id)
    provider.error = HighlightError("Provider rate limit reached.", 503)
    failure = finished(
        manager, manager.submit(recording.id, request(transcript, regenerate=True)).id
    )
    assert failure.status == "failed"
    assert failure.requests_made == 1
    assert provider.calls == 2
    state = manager.state(recording.id)
    assert state.latest.id == failure.id and state.result.id == old.id


def test_timeout_cancels_request_and_close_prevents_new_work(service):
    manager, recording, transcript, provider = service
    manager.settings = replace(manager.settings, highlight_request_seconds=0.1)
    provider.hold = True
    result = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert result.status == "failed" and "time limit" in result.error
    assert provider.cancelled and provider.calls == 1
    manager.close()
    with pytest.raises(HighlightError, match="stopping"):
        manager.submit(recording.id, request(transcript))


def test_empty_results_and_empty_transcript_are_valid(service):
    manager, recording, transcript, provider = service
    provider.empty = True
    result = finished(manager, manager.submit(recording.id, request(transcript)).id)
    assert result.status == "succeeded" and result.candidates == []
    assert result.requests_made == 1 and manager.reviewer.calls == 0
    with manager.store.connection() as db:
        db.execute("DELETE FROM transcript_words WHERE transcription_id=?", (transcript.id,))
        db.execute("UPDATE transcriptions SET revision=2,word_count=0 WHERE id=?", (transcript.id,))
    manager.settings = replace(manager.settings, openai_api_key="")
    result = finished(
        manager,
        manager.submit(
            recording.id,
            HighlightRequest(transcription_id=transcript.id, revision=2, remote_consent=True),
        ).id,
    )
    assert result.status == "succeeded" and result.total_windows == 0 and result.requests_made == 0
    assert provider.calls == 1


def test_preflight_rejects_missing_key_budget_and_wrong_transcript_without_calls(service):
    manager, recording, transcript, provider = service
    manager.settings = replace(manager.settings, openai_api_key="")
    with pytest.raises(HighlightError, match="OPENAI_API_KEY"):
        manager.submit(recording.id, request(transcript))
    manager.settings = replace(manager.settings, openai_api_key="test", highlight_max_input_bytes=1)
    with pytest.raises(HighlightError, match="budget"):
        manager.submit(recording.id, request(transcript))
    with pytest.raises(HighlightError):
        manager.submit(str(uuid4()), request(transcript))
    assert provider.calls == 0
    assert manager.state(recording.id).latest is None


def test_short_recording_and_wrong_transcript_are_rejected_before_processing(service):
    manager, recording, transcript, provider = service
    with pytest.raises(HighlightError, match="shorter"):
        manager.submit(recording.id, request(transcript, min_seconds=90, max_seconds=100))
    other = HighlightRequest(transcription_id=uuid4(), revision=1, remote_consent=True)
    with pytest.raises(HighlightError, match="current saved revision"):
        manager.submit(recording.id, other)
    assert provider.calls == 0


def test_recovery_preserves_prior_results_and_marks_interrupted_jobs_failed(service):
    manager, recording, transcript, provider = service
    prior = finished(manager, manager.submit(recording.id, request(transcript)).id)
    queued = HighlightJob(
        id=str(uuid4()),
        recording_id=recording.id,
        transcription_id=transcript.id,
        transcript_revision=1,
        model="synthetic",
        status="queued",
        total_windows=1,
        created_at=now(),
    )
    manager.results.create(queued, "unfinished")
    manager.results.recover()
    assert manager.results.job(queued.id).status == "failed"
    assert manager.state(recording.id).result.id == prior.id
    assert provider.calls == 1


def test_api_contract_and_review_conflicts_without_live_provider(service):
    manager, recording, transcript, _ = service
    app = FastAPI()
    app.include_router(create_highlight_router(manager))
    with TestClient(app) as client:
        config = client.get("/api/highlights/config")
        assert config.status_code == 200 and config.json()["configured"]
        assert "test-not-a-real-key" not in config.text
        url = f"/api/recordings/{recording.id}/highlights"
        body = request(transcript).model_dump(mode="json")
        assert client.post(url, json={**body, "remote_consent": False}).status_code == 422
        assert client.post(url, json={**body, "unexpected": True}).status_code == 422
        assert client.get("/api/recordings/not-a-uuid/highlights").status_code == 422
        assert client.get(f"/api/recordings/{uuid4()}/highlights").status_code == 404
        submitted = client.post(url, json=body)
        assert submitted.status_code == 202
        job = finished(manager, submitted.json()["id"])
        state = client.get(url).json()
        assert state["result"]["id"] == job.id
        candidate_url = f"/api/highlight-candidates/{job.candidates[0].id}"
        assert (
            client.patch(candidate_url, json={"revision": 0, "review": "rejected"}).status_code
            == 200
        )
        assert (
            client.patch(candidate_url, json={"revision": 0, "review": "accepted"}).status_code
            == 409
        )
        assert (
            client.patch(
                f"/api/highlight-candidates/{uuid4()}", json={"revision": 0, "review": "accepted"}
            ).status_code
            == 404
        )


def test_parallel_windows_are_bounded_and_cancelled_without_partial_publication(
    service, monkeypatch
):
    manager, recording, transcript, provider = service
    original = highlight_module.unused_windows
    monkeypatch.setattr(highlight_module, "unused_windows", lambda *a, **kw: original(*a, **kw) * 7)
    provider.hold = True
    pending = manager.submit(recording.id, request(transcript))
    deadline = time.monotonic() + 3
    while provider.calls < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert provider.calls == 3
    assert manager.results.job(pending.id).requests_made == 3
    manager.cancel(pending.id)
    result = finished(manager, pending.id)
    assert result.status == "cancelled" and not result.candidates
    assert provider.calls == 3 and provider.cancelled


def test_pool_keeps_unused_candidates_and_invalidates_changed_instructions(service):
    manager, recording, transcript, provider = service
    original = provider.suggest

    async def multiple(window, payload, *, context=None):
        result = await original(window, payload)
        assert payload.max_candidates == 5
        one = result.candidates.candidates[0]
        two = one.model_copy(
            update={
                "first_segment": window[8].id,
                "last_segment": window[12].id,
                "evidence": window[8].text,
                "score": 80,
            }
        )
        return ProviderResult(ProposedHighlights(candidates=[one, two]), 200, 100)

    provider.suggest = multiple
    first = finished(
        manager, manager.submit(recording.id, request(transcript, max_candidates=1)).id
    )
    assert first.status == "succeeded" and len(first.candidates) == 1
    assert len(manager.results.pool(first.pool_key)) == 2
    exclusions = [{"start": first.candidates[0].start, "end": first.candidates[0].end}]
    second = manager.submit(
        recording.id, request(transcript, max_candidates=1, excluded_ranges=exclusions)
    )
    assert second.status == "succeeded" and second.cache_hit
    assert second.requests_made == 0 and provider.calls == 1
    assert second.candidates[0].start >= first.candidates[0].end
    assert second.candidates[0].job_id == second.id
    manager.results.review(second.candidates[0].id, ReviewRequest(review="rejected", revision=0))
    assert second.candidates[0].fingerprint not in {
        candidate.fingerprint for candidate in manager.results.pool(first.pool_key)
    }
    provider.suggest = original
    third = finished(
        manager,
        manager.submit(
            recording.id,
            request(
                transcript,
                max_candidates=1,
                excluded_ranges=exclusions,
                ai_instructions="Prefer practical advice",
            ),
        ).id,
    )
    assert third.pool_key != first.pool_key and not third.cache_hit
    assert provider.calls == 2
