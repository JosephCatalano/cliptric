"""Supplied-clip orchestration with explicit synthetic media/transcripts and AI."""

import asyncio
import io
import json
import threading
import zipfile
from dataclasses import replace
from uuid import uuid4

import httpx2
import pytest
from fastapi import HTTPException

from backend.batch_titles import BatchTitles
from backend.highlight_models import HighlightError
from backend.media import MediaError
from backend.project_models import ItemEdit
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, seed
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools


def batch(recordings, **settings):
    return {
        "id": str(uuid4()), "title": "Campaign clips",
        "recording_ids": [recording.id for recording in recordings],
        "settings": {
            "duration_mode": "full", "analysis_end": max(r.duration for r in recordings),
            "caption_preset": "impact", "caption_position": "auto", **settings,
        },
    }


def edit_payload(item, **changes):
    return {**{name: item[name] for name in ItemEdit.model_fields}, **changes}


def test_separate_sources_transcripts_complete_lengths_reuse_delivery_and_edit(context, tmp_path):
    client, manager, first, provider, _ = context
    second = seed(manager, tmp_path)
    second = second.model_copy(update={"duration": 6.0, "name": "Short supplied.mp4"})
    manager.store.save_recording(second, ".mp4")
    request = batch([first, second])
    result = create(client, request)
    assert result["status"] == "ready", result
    assert not provider.calls and result["highlight_id"] is None
    items = result["items"]
    assert [(item["start"], item["end"]) for item in items] == [(0, 80), (0, 6)]
    assert [item["export"]["recording_id"] for item in items] == [first.id, second.id]
    assert len({item["transcription_id"] for item in items}) == 2
    for item in items:
        assert item["export"]["captions"]["transcription_id"] == item["transcription_id"]
        assert manager.store.source_path(item["recording_id"]).exists()
    assert items[1]["title"] == "Short supplied"
    assert client.post("/api/projects", json=request).json()["id"] == result["id"]
    assert client.post("/api/projects", json={**request, "recording_ids": list(reversed(request["recording_ids"]))}).status_code == 409
    previous_exports = [item["export_id"] for item in items]
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert [item["export_id"] for item in resumed["items"]] == previous_exports
    assert len(manager.media.renders) == 2
    bundle = client.get(f"/api/projects/{result['id']}/bundle")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["included"]) == 2 and manifest["excluded"] == []
        assert {item["recording_id"] for item in manifest["included"]} == {first.id, second.id}
    route = f"/api/projects/{result['id']}/clips/{items[1]['id']}"
    # The second clip must never inherit the first source's longer trim range.
    assert client.patch(route, json=edit_payload(items[1], end=7.0)).status_code == 422
    assert client.patch(route, json=edit_payload(items[1], title="Edited short", start=1.0, end=5.0)).status_code == 202
    edited = finished(client, result["id"])
    assert edited["status"] == "ready", edited
    assert edited["items"][0]["export_id"] == previous_exports[0]
    assert edited["items"][1]["export"]["recording_id"] == second.id
    assert edited["items"][1]["export"]["headline"]["text"] == "Edited short"
    assert all(manager.store.export_path(identifier).exists() for identifier in previous_exports)
    assert client.patch(route, json=edit_payload(items[1], title="Stale window")).status_code == 409


def test_failure_isolated_and_resume_reuses_success(context, tmp_path, monkeypatch):
    client, manager, first, _, _ = context
    second = seed(manager, tmp_path)
    render = manager.media.render

    def fail_one(source, *args, **kwargs):
        if source == manager.store.source_path(second.id):
            raise MediaError("Synthetic second-source failure")
        return render(source, *args, **kwargs)

    monkeypatch.setattr(manager.media, "render", fail_one)
    result = create(client, batch([first, second]))
    assert result["status"] == "partial"
    assert result["items"][0]["export"]["status"] == "succeeded"
    assert "Synthetic second-source failure" in result["items"][1]["error"]
    bundle = client.get(f"/api/projects/{result['id']}/bundle")
    assert bundle.headers["x-clips-included"] == "1"
    assert bundle.headers["x-clips-excluded"] == "1"
    monkeypatch.setattr(manager.media, "render", render)
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert resumed["status"] == "ready"
    assert resumed["items"][0]["export_id"] == result["items"][0]["export_id"]


@pytest.mark.parametrize("changes", [
    {"recording_ids": []}, {"recording_id": str(uuid4())},
    {"generate_titles": True}, {"generate_titles": True, "remote_consent": "yes"},
])
def test_source_and_consent_validation(context, changes):
    client, _, recording, _, _ = context
    assert client.post("/api/projects", json={**batch([recording]), **changes}).status_code == 422


def test_batch_bounds_unknown_missing_duplicate_and_size(context):
    client, manager, recording, _, _ = context
    request = batch([recording])
    for ids in ([recording.id] * 2, [str(uuid4()) for _ in range(16)]):
        assert client.post("/api/projects", json={**request, "recording_ids": ids}).status_code == 422
    assert client.post("/api/projects", json={**request, "recording_ids": [str(uuid4())]}).status_code == 404
    assert client.post("/api/projects", json=batch([recording], analysis_start=1.0)).status_code == 422
    manager.store.save_recording(recording.model_copy(update={"duration": 121.0}), ".mp4")
    assert client.post("/api/projects", json=request).status_code == 422
    manager.store.save_recording(recording.model_copy(update={"size_bytes": 2 * 1024**3 + 1}), ".mp4")
    assert client.post("/api/projects", json=request).status_code == 413
    manager.store.save_recording(recording, ".mp4")
    manager.store.source_path(recording.id).unlink()
    assert client.post("/api/projects", json=request).status_code == 404


def test_ai_title_failure_never_automatically_retries_and_manual_title_wins(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def fail(words):
        calls.append(words)
        raise HighlightError("Synthetic unknown billing outcome", 503)

    monkeypatch.setattr(manager.titles, "suggest", fail)
    request = {**batch([recording]), "generate_titles": True, "remote_consent": True}
    result = create(client, request)
    assert result["status"] == "ready" and len(calls) == 1
    item = result["items"][0]
    assert item["title_attempted"] and item["title_error"] and item["title"] == "Synthetic"
    client.post(f"/api/projects/{result['id']}/resume", json={})
    finished(client, result["id"])
    assert len(calls) == 1

    async def succeed(words):
        calls.append(words)
        return "A grounded title", 100, 20

    monkeypatch.setattr(manager.titles, "suggest", succeed)
    client.post(f"/api/projects/{result['id']}/resume", json={"retry_titles": True})
    retried = finished(client, result["id"])
    item = retried["items"][0]
    assert len(calls) == 2 and item["title_complete"] and not item["title_error"]
    assert item["title_input_tokens"] == 100
    assert item["export"]["headline"]["text"] == "A grounded title"
    assert manager.store.export_path(result["items"][0]["export_id"]).exists()
    assert client.patch(f"/api/projects/{result['id']}/clips/{item['id']}", json=edit_payload(item, title="Manual title")).status_code == 202
    finished(client, result["id"])
    client.post(f"/api/projects/{result['id']}/resume", json={"retry_titles": True})
    assert finished(client, result["id"])["items"][0]["title"] == "Manual title"
    assert len(calls) == 2


def test_cancel_during_title_keeps_durable_result_and_resume_does_not_rebill(context, monkeypatch):
    client, manager, recording, _, _ = context
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def held(words):
        calls.append(words)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "Durable title", 20, 10

    monkeypatch.setattr(manager.titles, "suggest", held)
    response = client.post("/api/projects", json={**batch([recording]), "generate_titles": True, "remote_consent": True})
    identifier = response.json()["id"]
    try:
        assert entered.wait(3)
        assert client.post(f"/api/projects/{identifier}/cancel").status_code == 200
    finally:
        release.set()
    result = finished(client, identifier)
    assert result["status"] == "cancelled"
    assert result["items"][0]["title_complete"] and result["items"][0]["export_id"] is None
    client.post(f"/api/projects/{identifier}/resume", json={})
    assert finished(client, identifier)["status"] == "ready"
    assert len(calls) == 1


def test_restart_preserves_finished_exports_and_blocks_unknown_title_retry(context):
    client, manager, recording, _, _ = context
    result = create(client, batch([recording]))
    project = manager.get(result["id"], hydrate=False)
    project.status, project.generate_titles = "rendering", True
    project.items[0].title_attempted = True
    manager.records.save(project)
    manager.records.initialize()
    interrupted = manager.get(project.id)
    assert interrupted.status == "interrupted"
    assert interrupted.items[0].export_id == result["items"][0]["export_id"]
    client.post(f"/api/projects/{project.id}/resume", json={})
    assert finished(client, project.id)["items"][0]["export_id"] == result["items"][0]["export_id"]


def test_silent_clip_recovers_by_explicitly_disabling_captions(context):
    client, manager, recording, _, _ = context
    recording = recording.model_copy(update={"has_audio": False})
    manager.store.save_recording(recording, ".mp4")
    result = create(client, batch([recording]))
    assert result["status"] == "partial" and "no audio" in result["items"][0]["error"]
    item = result["items"][0]
    response = client.patch(f"/api/projects/{result['id']}/clips/{item['id']}", json=edit_payload(item, caption_preset="none"))
    assert response.status_code == 202, response.text
    assert finished(client, result["id"])["status"] == "ready"


def title_response(title="Source grounded"):
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"title": title})}]}], "usage": {"input_tokens": 20, "output_tokens": 8}}


def test_title_transport_is_fixed_bounded_structured_and_has_no_retry(settings):
    calls = []

    def respond(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.url == "https://api.openai.com/v1/responses"
        assert body["store"] is False and body["text"]["format"]["strict"] is True
        assert "untrusted DATA" in body["instructions"]
        assert json.loads(body["input"])["transcript"] == "Actual speech"
        return httpx2.Response(200, json=title_response())

    provider = BatchTitles(replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond))
    assert asyncio.run(provider.suggest([{"text": "Actual speech"}])) == ("Source grounded", 20, 8)
    assert len(calls) == 1
    with pytest.raises(HighlightError):
        asyncio.run(provider.suggest([{"text": "x" * (64 * 1024 + 1)}]))
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    {}, {"status": "incomplete"}, title_response(" "), title_response("x" * 121),
    {**title_response(), "usage": {"input_tokens": True, "output_tokens": 8}},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
])
def test_invalid_titles_fail_without_publishing(response):
    with pytest.raises(HighlightError):
        BatchTitles.parse(json.dumps(response).encode())
