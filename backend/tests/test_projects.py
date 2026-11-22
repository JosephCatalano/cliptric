"""Project lifecycle tests with explicitly synthetic provider/media adapters."""

import asyncio
import io
import json
import threading
import time
import zipfile
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.highlight_models import ProposedHighlights, ProviderResult
from backend.media import MediaInfo, RenderCancelled
from backend.models import Recording
from backend.storage import now
from backend.tests.editorial_fixtures import SyntheticEditorialReviewer
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


class SyntheticMedia:
    def __init__(self, settings):
        self.settings = settings
        self.renders = []
        self.fail_start = None

    def thumbnail(self, source, output, timestamp, cancel):
        output.write_bytes(b"synthetic-thumbnail")

    def render(self, source, output, start, end, mode, cancel, log_path, **kwargs):
        from backend.media import MediaError

        self.renders.append((start, end))
        if cancel.is_set():
            raise RenderCancelled()
        if self.fail_start == start:
            raise MediaError("Synthetic render failure")
        output.write_bytes(b"synthetic-mp4")
        return MediaInfo(end - start, 1080, 1920, True)


class SyntheticProvider:
    def __init__(self):
        self.calls = []
        self.hold = False
        self.entered = threading.Event()
        self.empty = False

    async def suggest(self, window, request):
        self.calls.append((window, request))
        self.entered.set()
        while self.hold:
            await asyncio.sleep(0.01)
        candidates = (
            []
            if self.empty
            else [
                dict(
                    first_segment=window[0].id,
                    last_segment=window[1].id,
                    score=90,
                    title="Synthetic first",
                    reason="Complete synthetic passage",
                    evidence=window[0].text,
                    category="insight",
                ),
                dict(
                    first_segment=window[3].id,
                    last_segment=window[5].id,
                    score=80,
                    title="Synthetic second",
                    reason="Second synthetic passage",
                    evidence=window[3].text,
                    category="story",
                ),
            ]
        )
        return ProviderResult(ProposedHighlights(candidates=candidates), 100, 40)


def seed(manager, tmp_path):
    recording = Recording(
        id=str(uuid4()),
        name="Synthetic.mp4",
        duration=80,
        width=320,
        height=180,
        has_audio=True,
        size_bytes=100,
        created_at=now(),
    )
    manager.store.save_recording(recording, ".mp4")
    manager.store.source_path(recording.id).write_bytes(b"synthetic-source")
    transcript = manager.transcriptions.transcripts.create(str(uuid4()), recording.id)
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
    path = tmp_path / "words.ndjson"
    path.write_text("".join(json.dumps(w) + "\n" for w in words), encoding="utf-8")
    manager.transcriptions.transcripts.publish(
        transcript.id, "source", "config", path, 80, len(words)
    )
    return recording


@pytest.fixture
def context(settings, tmp_path, monkeypatch):
    provider = SyntheticProvider()
    monkeypatch.setattr(main, "OpenAIHighlights", lambda settings: provider)
    monkeypatch.setattr(main, "EditorialReviewer", SyntheticEditorialReviewer)
    monkeypatch.setattr(main, "Media", SyntheticMedia)
    configured = replace(settings, openai_api_key="synthetic")
    with TestClient(main.create_app(configured), base_url="http://127.0.0.1:8765") as client:
        manager = client.app.state.projects
        recording = seed(manager, tmp_path)
        yield client, manager, recording, provider, configured


def payload(recording, **settings):
    return {
        "id": str(uuid4()),
        "recording_id": recording.id,
        "title": "Saved project",
        "settings": {"analysis_start": 10.0, "analysis_end": 65.0, **settings},
    }


def finished(client, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = client.get(f"/api/projects/{identifier}").json()
        if value["status"] not in {
            "queued",
            "downloading",
            "transcribing",
            "analysing",
            "rendering",
            "cancelling",
        }:
            return value
        time.sleep(0.03)
    pytest.fail("Project did not finish")


def create(client, request):
    response = client.post("/api/projects", json=request)
    assert response.status_code == 202, response.text
    return finished(client, response.json()["id"])


def test_auto_range_variable_lengths_cache_idempotency_and_zip(context):
    client, manager, recording, provider, _ = context
    request = payload(recording)
    result = create(client, request)
    assert result["status"] == "ready", result
    assert set(result["stage_seconds"]) >= {"transcription", "ai", "render", "thumbnails", "visual"}
    assert all(value >= 0 for value in result["stage_seconds"].values())
    saved = manager.records.get(result["id"])
    assert saved.stage_seconds == result["stage_seconds"]
    assert [i["end"] - i["start"] for i in result["items"]] == [9, 14]
    assert all(10 <= i["start"] < i["end"] <= 65 for i in result["items"])
    assert len(provider.calls) == 1
    window, options = provider.calls[0]
    assert min(s.start for s in window) >= 10 and max(s.end for s in window) <= 65
    assert options.duration_mode == "auto" and (options.min_seconds, options.max_seconds) == (
        8,
        55,
    )
    assert client.post("/api/projects", json=request).json()["id"] == result["id"]
    assert client.post("/api/projects", json={**request, "title": "Different"}).status_code == 409
    assert client.post(f"/api/projects/{result['id']}/resume", json={}).status_code == 202
    finished(client, result["id"])
    assert len(manager.media.renders) == 2 and len(provider.calls) == 1
    bundle = client.get(f"/api/projects/{result['id']}/bundle")
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["included"]) == 2 and not manifest["excluded"]
        assert len([n for n in archive.namelist() if n.endswith(".mp4")]) == 2
        assert all("/" not in n for n in archive.namelist())
    assert client.get(f"/api/projects/{result['id']}/bundle?ids={uuid4()}").status_code == 404
    assert client.get(f"/api/projects/{result['id']}/bundle?ids=../../secret").status_code == 422
    selected = client.get(f"/api/projects/{result['id']}/bundle?ids={result['items'][0]['id']}")
    assert selected.status_code == 200 and selected.headers["X-Clips-Included"] == "1"


def test_manual_bounds_and_failed_clip_retry_preserve_ready_outputs(context):
    client, manager, recording, provider, _ = context
    manager.media.fail_start = 25
    result = create(
        client, payload(recording, duration_mode="manual", min_seconds=8, max_seconds=10)
    )
    assert result["status"] == "ready" and len(result["items"]) == 1
    assert provider.calls[0][1].max_seconds == 10
    # Broader manual bounds keep both candidates; one deterministic render fails.
    request = payload(recording, duration_mode="manual", min_seconds=8, max_seconds=20)
    result = create(client, request)
    assert result["status"] == "partial", result
    first = result["items"][0]["export_id"]
    bundle = client.get(f"/api/projects/{result['id']}/bundle")
    assert bundle.headers["X-Clips-Excluded"] == "1"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert len(json.loads(archive.read("manifest.json"))["excluded"]) == 1
    count, calls = len(manager.media.renders), len(provider.calls)
    manager.media.fail_start = None
    client.post(f"/api/projects/{result['id']}/resume", json={})
    retried = finished(client, result["id"])
    assert retried["status"] == "ready"
    assert retried["items"][0]["export_id"] == first
    assert len(manager.media.renders) == count + 1 and len(provider.calls) == calls


def test_animated_caption_emphasis_is_saved_and_rerenders_only_edited_clip(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording, caption_preset="flow", emphasis_words=["Evidence"]))
    assert result["status"] == "ready", result
    first, other = result["items"]
    assert result["settings"]["emphasis_words"] == ["Evidence"]
    assert first["export"]["captions"]["preset"] == "flow"
    assert first["export"]["captions"]["emphasis_words"] == ["Evidence"]
    original = manager.store.clip(first["export_id"])
    original_words = manager.store.caption_words(first["export_id"])
    renders, calls = len(manager.media.renders), len(provider.calls)
    edit = {
        key: first[key]
        for key in (
            "title",
            "start",
            "end",
            "revision",
            "review",
            "caption_preset",
            "caption_position",
            "emphasis_words",
        )
    }
    edit.update(caption_preset="glow", emphasis_words=["other"])
    response = client.patch(f"/api/projects/{result['id']}/clips/{first['id']}", json=edit)
    assert response.status_code == 202, response.text
    changed = finished(client, result["id"])
    assert changed["status"] == "ready", changed
    assert changed["items"][0]["export"]["captions"]["preset"] == "glow"
    assert changed["items"][0]["export"]["captions"]["emphasis_words"] == ["other"]
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clip(first["export_id"]) == original
    assert manager.store.caption_words(first["export_id"]) == original_words
    assert len(manager.media.renders) == renders + 1 and len(provider.calls) == calls
    restored = manager.records.get(result["id"])
    assert restored.settings.emphasis_words == ["Evidence"] and restored.items[
        0
    ].emphasis_words == ["other"]


def test_trim_title_conflict_bounds_and_caption_override(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    item, other = result["items"]
    url = f"/api/projects/{result['id']}/clips/{item['id']}"
    edit = {
        k: item[k]
        for k in ("title", "start", "end", "revision", "caption_preset", "caption_position")
    }
    assert client.patch(url, json={**edit, "start": 0.0}).status_code == 422
    title = client.patch(url, json={**edit, "title": "Edited title"})
    assert title.status_code == 202
    titled = finished(client, result["id"])
    assert titled["items"][0]["export"]["headline"]["text"] == "Edited title"
    assert titled["items"][1]["export_id"] == other["export_id"]
    assert len(manager.media.renders) == 3
    assert client.patch(url, json=edit).status_code == 409
    edit.update(title="Edited title", revision=1, start=11.0, caption_preset="boxed")
    assert client.patch(url, json=edit).status_code == 202
    changed = finished(client, result["id"])
    assert changed["items"][0]["title"] == "Edited title"
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert changed["items"][0]["export_id"] != item["export_id"]
    assert manager.store.clip(item["export_id"]).status == "succeeded"
    assert len(manager.media.renders) == 4 and len(provider.calls) == 1
    assert changed["items"][0]["export"]["captions"]["preset"] == "boxed"


def test_opening_headlines_default_and_disable_preserves_original(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    item, other = result["items"]
    original = manager.store.clip(item["export_id"])
    assert original.headline.text == item["title"]
    assert original.headline.seconds == 8
    edit = {
        k: item[k]
        for k in ("title", "start", "end", "revision", "caption_preset", "caption_position")
    }
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{item['id']}", json={**edit, "headline_enabled": False}
    )
    assert response.status_code == 202, response.text
    changed = finished(client, result["id"])
    assert changed["items"][0]["export"]["headline"] is None
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clip(original.id) == original
    assert len(provider.calls) == 1


def test_updating_legacy_headline_adds_fades_without_repeating_analysis(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    item, other = result["items"]
    old_id = item["export_id"]
    original_bytes = manager.store.export_path(old_id).read_bytes()
    with manager.store.connection() as db:
        old = json.loads(
            db.execute("SELECT options FROM clip_headlines WHERE clip_id=?", (old_id,)).fetchone()[
                0
            ]
        )
        del old["fade_ms"]
        db.execute("UPDATE clip_headlines SET options=? WHERE clip_id=?", (json.dumps(old), old_id))
    assert client.post(f"/api/projects/{result['id']}/resume", json={}).status_code == 202
    changed = finished(client, result["id"])
    assert changed["items"][0]["export_id"] != old_id
    assert changed["items"][0]["export"]["headline"]["fade_ms"] == 250
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clip(old_id).headline.fade_ms == 0
    assert manager.store.export_path(old_id).read_bytes() == original_bytes
    assert len(provider.calls) == 1 and len(manager.media.renders) == 3
    client.post(f"/api/projects/{result['id']}/resume", json={})
    finished(client, result["id"])
    assert len(provider.calls) == 1 and len(manager.media.renders) == 3


def test_cancel_inflight_ai_requires_explicit_retry(context):
    client, manager, recording, provider, _ = context
    provider.hold = True
    request = payload(recording)
    response = client.post("/api/projects", json=request)
    assert provider.entered.wait(5)
    identifier = response.json()["id"]
    client.post(f"/api/projects/{identifier}/cancel")
    result = finished(client, identifier)
    assert result["status"] == "cancelled"
    assert len(provider.calls) == 1 and not manager.media.renders
    assert client.post(f"/api/projects/{identifier}/resume", json={}).status_code == 409
    provider.hold = False
    client.post(f"/api/projects/{identifier}/resume", json={"retry_analysis": True})
    assert finished(client, identifier)["status"] == "ready"
    assert len(provider.calls) == 2


def test_empty_result_and_invalid_settings(context):
    client, _, recording, provider, _ = context
    for options in (
        {"analysis_end": 81.0},
        {"analysis_start": float("inf")},
        {"duration_mode": "manual", "min_seconds": 30, "max_seconds": 15},
        {"max_clips": 21},
        {"caption_preset": "untrusted"},
    ):
        if options.get("analysis_start") == float("inf"):
            # JSON cannot carry Infinity through the standard test client encoder.
            response = client.post(
                "/api/projects",
                content=json.dumps(payload(recording, **options)),
                headers={"content-type": "application/json"},
            )
        else:
            response = client.post("/api/projects", json=payload(recording, **options))
        assert response.status_code == 422
    provider.empty = True
    result = create(client, payload(recording))
    assert result["status"] == "ready" and result["selection_complete"] and not result["items"]
    assert client.get(f"/api/projects/{result['id']}/bundle").status_code == 409


def test_restart_recovery_preserves_settings_and_results(context):
    client, manager, recording, _, configured = context
    result = create(client, payload(recording, caption_preset="bold"))
    saved = manager.get(result["id"], hydrate=False)
    saved.status = "rendering"  # Simulate persisted state left by an abrupt shutdown.
    manager.records.save(saved)
    # Independent store recovery is additive and does not schedule paid work.
    from backend.project_store import ProjectStore

    restored = ProjectStore(manager.store)
    restored.initialize()
    project = restored.get(result["id"])
    assert project.status == "interrupted"
    assert project.settings.caption_preset == "bold"
    assert [i.export_id for i in project.items] == [i["export_id"] for i in result["items"]]
    assert configured.data_dir.exists()


def test_bounded_queue_cancel_and_resume_without_duplicate_scheduling(context):
    client, manager, recording, provider, _ = context
    provider.hold = True
    first = client.post("/api/projects", json=payload(recording)).json()
    assert provider.entered.wait(5)
    queued = [client.post("/api/projects", json=payload(recording)).json() for _ in range(7)]
    assert client.post("/api/projects", json=payload(recording)).status_code == 409
    identifier = queued[-1]["id"]
    assert client.post(f"/api/projects/{identifier}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/projects/{identifier}/resume", json={}).status_code == 202
    with manager.lock:
        assert len(manager.scheduled) == 8
    for project in queued:
        client.post(f"/api/projects/{project['id']}/cancel")
    client.post(f"/api/projects/{first['id']}/cancel")
    assert finished(client, first["id"])["status"] == "cancelled"
    assert len(provider.calls) == 1


def test_project_reviews_persist_without_rerender_and_control_batch_delivery(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    item = result["items"][0]
    edit = {
        k: item[k]
        for k in ("title", "start", "end", "revision", "caption_preset", "caption_position")
    }
    url = f"/api/projects/{result['id']}/clips/{item['id']}"
    assert client.patch(url, json={**edit, "review": "rejected"}).status_code == 202
    assert len(manager.media.renders) == 2
    assert client.get(f"/api/projects/{result['id']}").json()["items"][0]["review"] == "rejected"
    from backend.project_store import ProjectStore

    assert ProjectStore(manager.store).get(result["id"]).items[0].review == "rejected"
    bundle = client.get(f"/api/projects/{result['id']}/bundle")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["included"]) == 1
        assert manifest["excluded"][0]["reason"] == "rejected in project review"


@pytest.mark.parametrize("fail_on_start", [True, False])
def test_zip_disconnect_always_releases_resources(fail_on_start):
    from starlette.requests import ClientDisconnect

    from backend.project_routes import BundleResponse

    cleaned = threading.Event()

    async def send(message):
        if fail_on_start or message["type"] == "http.response.body":
            raise OSError("Synthetic client disconnect")

    async def receive():
        return {"type": "http.disconnect"}

    response = BundleResponse(iter([b"test"]), cleanup=cleaned.set)
    with pytest.raises(ClientDisconnect):
        asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
    assert cleaned.is_set()


def test_fresh_source_transcription_handoff_then_reuse(context, tmp_path, monkeypatch):
    client, manager, _, provider, _ = context
    recording = Recording(
        id=str(uuid4()),
        name="Fresh synthetic.mp4",
        duration=80,
        width=320,
        height=180,
        has_audio=True,
        size_bytes=100,
        created_at=now(),
    )
    manager.store.save_recording(recording, ".mp4")
    manager.store.source_path(recording.id).write_bytes(b"synthetic-source")
    calls = []

    def synthetic_inference(job):
        # Substitute only inference. Project submission, state, waiting, and persistence are real.
        try:
            calls.append(job.id)
            manager.transcriptions.transcripts.publish(
                job.id, "synthetic-new-source", "config", tmp_path / "words.ndjson", 80, 16
            )
        finally:
            with manager.transcriptions.lock:
                manager.transcriptions.active = None

    monkeypatch.setattr(manager.transcriptions, "run", synthetic_inference)
    result = create(client, payload(recording))
    assert result["status"] == "ready" and result["transcript_revision"] == 1
    assert result["transcription_id"] == calls[0]
    assert len(provider.calls) == 1
    again = create(client, payload(recording))
    assert again["status"] == "ready" and again["transcription_id"] == calls[0]
    assert len(calls) == 1 and len(provider.calls) == 1
