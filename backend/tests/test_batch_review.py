"""AI keep/trim review with explicit synthetic media, transcripts and provider data."""

import asyncio
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import httpx2
import pytest

from backend.batch_review import BatchReview, ReviewProposal
from backend.highlight_models import HighlightError
from backend.highlight_selection import Segment
from backend.project_models import ItemEdit
from backend.tests.test_batches import batch, create, edit, update_recording
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import finished, payload


def proposal(decision="keep_full", *, bad=False):
    first, last = (0, 15) if decision == "keep_full" else (4, 10)
    return ReviewProposal(
        decision=decision,
        reason="Synthetic complete setup and resolved payoff.",
        candidate={
            "first_segment": 999 if bad else first,
            "last_segment": last,
            "title": "A complete source-grounded story",
            "score": 80,
            "reason": "Synthetic source-grounded passage",
            "evidence": f"Evidence {first}.",
            "category": "story",
            "quality": {
                "hook": 4,
                "context": 4,
                "payoff": 4,
                "standalone": 4,
                "opening_evidence": f"Evidence {first}.",
                "ending_evidence": f"Evidence {last}.",
            },
        },
    )


def auto_request(recording):
    return {**batch([recording], batch_mode="auto", quality_mode=True), "remote_consent": True}


@pytest.mark.parametrize("decision", ["keep_full", "trim"])
def test_eight_minute_keep_or_trim_persist_and_resume_without_rebilling(
    context, monkeypatch, decision
):
    client, manager, recording, _, _ = context
    recording = recording.model_copy(update={"duration": 480.0})
    update_recording(manager, recording)
    calls = []

    async def review(segments, duration, **kwargs):
        calls.append((segments, duration, kwargs))
        return proposal(decision), 100, 30

    monkeypatch.setattr(manager.batch_review, "suggest", review)
    request = auto_request(recording)
    request["settings"]["ai_instructions"] = "Preserve complete stories."
    result = create(client, request)
    assert result["status"] == "ready", result
    item = result["items"][0]
    assert item["analysis_complete"] and item["analysis_choice"] == decision
    assert item["analysis_reason"] and item["analysis_input_tokens"] == 100
    assert item["title_complete"] and item["export"]["recording_id"] == recording.id
    if decision == "keep_full":
        assert (item["start"], item["end"], item["export"]["duration"]) == (0, 480, 480)
    else:
        assert 19 < item["start"] <= 20 and 54 <= item["end"] < 55
    assert calls[0][1] == 480 and calls[0][2]["ai_instructions"] == "Preserve complete stories."
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert resumed["items"][0]["export_id"] == item["export_id"] and len(calls) == 1
    stored = manager.records.get(result["id"])
    assert stored.items[0].analysis_choice == decision
    route = f"/api/projects/{result['id']}/clips/{item['id']}"
    assert client.patch(route, json=edit(item, start=0.0, end=300.0)).status_code == 202
    edited = finished(client, result["id"])
    assert edited["status"] == "ready" and edited["items"][0]["export"]["duration"] == 300
    assert manager.store.export_path(item["export_id"]).exists() and len(calls) == 1


def test_auto_review_requires_explicit_consent(context):
    client, _, recording, _, _ = context
    request = auto_request(recording)
    for value in (False, "yes", 1):
        assert (
            client.post("/api/projects", json={**request, "remote_consent": value}).status_code
            == 422
        )


def test_long_form_auto_uses_source_analysis_bound_instead_of_two_minutes(context):
    client, manager, recording, provider, _ = context
    recording = recording.model_copy(update={"duration": 480.0})
    update_recording(manager, recording)
    result = create(client, payload(recording, analysis_start=0.0, analysis_end=480.0))
    assert result["status"] == "ready", result
    assert provider.calls[0][1].max_seconds == 480


def test_failed_review_requires_explicit_retry_or_manual_override(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def fail(*args, **kwargs):
        calls.append(True)
        raise HighlightError("Synthetic unknown billing outcome", 503)

    monkeypatch.setattr(manager.batch_review, "suggest", fail)
    result = create(client, auto_request(recording))
    assert result["status"] == "partial" and result["items"][0]["analysis_attempted"]
    client.post(f"/api/projects/{result['id']}/resume", json={})
    assert finished(client, result["id"])["status"] == "partial" and len(calls) == 1

    async def succeed(*args, **kwargs):
        calls.append(True)
        return proposal(), 20, 10

    monkeypatch.setattr(manager.batch_review, "suggest", succeed)
    client.post(f"/api/projects/{result['id']}/resume", json={"retry_analysis": True})
    assert finished(client, result["id"])["status"] == "ready" and len(calls) == 2
    monkeypatch.setattr(manager.batch_review, "suggest", fail)
    another = create(client, auto_request(recording))
    item = another["items"][0]
    assert (
        client.patch(
            f"/api/projects/{another['id']}/clips/{item['id']}", json=edit(item)
        ).status_code
        == 202
    )
    manual = finished(client, another["id"])
    assert manual["status"] == "ready" and manual["items"][0]["analysis_choice"] == "manual"
    assert len(calls) == 3


@pytest.mark.parametrize("decision,bad", [("keep_full", True), ("trim", True), ("review", False)])
def test_unsupported_or_review_only_results_never_render(context, monkeypatch, decision, bad):
    client, manager, recording, _, _ = context

    async def review(*args, **kwargs):
        value = (
            ReviewProposal(
                decision="review", candidate=None, reason="No complete useful moment found."
            )
            if decision == "review"
            else proposal(decision, bad=bad)
        )
        return value, 20, 10

    monkeypatch.setattr(manager.batch_review, "suggest", review)
    result = create(client, auto_request(recording))
    assert result["status"] == "partial" and result["items"][0]["error"]
    assert result["items"][0]["export_id"] is None and not manager.media.renders


def test_review_word_budget_stops_before_provider(context, monkeypatch):
    client, manager, recording, _, _ = context
    monkeypatch.setattr(
        manager.transcriptions.transcripts,
        "words",
        lambda *args, **kwargs: SimpleNamespace(words=[None] * 6001),
    )
    result = create(client, auto_request(recording))
    assert result["status"] == "partial" and "6,000" in result["items"][0]["error"]
    assert not result["items"][0]["analysis_attempted"]


def test_cancel_during_review_preserves_decision_for_resume(context, monkeypatch):
    client, manager, recording, _, _ = context
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def review(*args, **kwargs):
        calls.append(True)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return proposal(), 20, 10

    monkeypatch.setattr(manager.batch_review, "suggest", review)
    response = client.post("/api/projects", json=auto_request(recording))
    identifier = response.json()["id"]
    try:
        assert entered.wait(3)
        active = client.get(f"/api/projects/{identifier}").json()
        assert active["status"] == "analysing" and "AI reviewing" in active["stage"]
        client.post(f"/api/projects/{identifier}/cancel")
    finally:
        release.set()
    stopped = finished(client, identifier)
    assert stopped["status"] == "cancelled" and stopped["items"][0]["analysis_complete"]
    assert stopped["items"][0]["export_id"] is None
    client.post(f"/api/projects/{identifier}/resume", json={})
    assert finished(client, identifier)["status"] == "ready" and len(calls) == 1


def test_review_transport_has_complete_source_duration_schema_and_preferences(settings):
    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        data = json.loads(body["input"])
        assert str(request.url) == "https://api.openai.com/v1/responses" and body["store"] is False
        assert (
            data["duration_seconds"] == 480
            and data["project_instructions"] == "Keep useful detail."
        )
        assert data["segments"][0]["id"] == 0 and body["text"]["format"]["strict"] is True
        return httpx2.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": proposal().model_dump_json()}],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 10},
            },
        )

    provider = BatchReview(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    result, _, _ = asyncio.run(
        provider.suggest(
            [Segment(0, 0, 10, "Actual speech.")],
            480,
            ai_instructions="Keep useful detail.",
            quality_mode=True,
        )
    )
    assert result.decision == "keep_full" and len(calls) == 1


@pytest.mark.parametrize(
    "data", [{}, {"status": "incomplete"}, {"status": "completed", "output": []}]
)
def test_incomplete_review_rejected(data):
    with pytest.raises(HighlightError):
        BatchReview.parse(json.dumps(data).encode())


def test_review_refusal_is_explicit_and_sanitized():
    data = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "private provider details"}],
            }
        ],
    }
    with pytest.raises(HighlightError, match="declined") as caught:
        BatchReview.parse(json.dumps(data).encode())
    assert "private provider details" not in str(caught.value)


def test_edit_contract_accepts_eight_minutes_but_still_rejects_invalid_ranges():
    values = {
        "revision": 0,
        "title": "An eight minute story",
        "start": 0.0,
        "end": 480.0,
        "caption_preset": "none",
        "caption_position": "auto",
    }
    assert ItemEdit(**values).end == 480
    for end in (-1.0, float("nan"), 21601.0):
        with pytest.raises(ValueError):
            ItemEdit(**{**values, "end": end})


@pytest.mark.parametrize("status", [302, 429, 503])
def test_review_transport_does_not_redirect_retry_or_expose_provider_details(settings, status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(
            status,
            text="private provider details",
            headers={"Location": "https://untrusted.invalid"},
        )

    provider = BatchReview(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    with pytest.raises(HighlightError) as caught:
        asyncio.run(
            provider.suggest(
                [Segment(0, 0, 10, "Actual speech.")], 480, ai_instructions="", quality_mode=False
            )
        )
    assert len(calls) == 1 and "private provider details" not in str(caught.value)


def test_review_response_is_bounded(settings):
    provider = BatchReview(
        replace(settings, openai_api_key="synthetic"),
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=b"x" * (256 * 1024 + 1))
        ),
    )
    with pytest.raises(HighlightError, match="safety limit"):
        asyncio.run(
            provider.suggest(
                [Segment(0, 0, 10, "Actual speech.")], 480, ai_instructions="", quality_mode=False
            )
        )
