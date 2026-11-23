"""Deferred source orchestration with explicitly synthetic media and provider adapters."""

import json
import sqlite3
import threading
import time
from uuid import uuid4

import pytest

import backend.youtube_worker as worker
from backend.media import MediaError, MediaInfo, RenderCancelled
from backend.project_models import Project, ProjectSettings
from backend.storage import now
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import finished, payload
from backend.tests.test_youtube_imports import URL, format_info
from backend.youtube_imports import YouTubeImports, YouTubeRequest


@pytest.fixture
def deferred(context, tmp_path, monkeypatch):
    client, manager, recording, provider, configured = context
    control = {"hold": False, "fail": False, "duration": 80.0}
    entered, release = threading.Event(), threading.Event()
    events = []

    def process(self, command, job, work, deadline, **kwargs):
        request = json.loads((work / "request.json").read_text())
        if request.get("metadata_only"):
            events.append("preview")
            result = {"name": "Deferred source", "duration": 80.0, "has_audio": True}
        else:
            events.append("download")
            entered.set()
            while control["hold"] and not release.wait(0.01):
                if self.cancel_event.is_set():
                    raise RenderCancelled()
            if control["fail"]:
                raise MediaError("Synthetic download failure")
            (work / "video.mp4").write_bytes(b"synthetic-video")
            result = {"title": "Deferred source", "files": ["video.mp4"]}
        (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return 0

    def transcribe(recording_id, *, identifier, allow_no_audio=False):
        assert not allow_no_audio
        events.append("transcribe")
        transcript = manager.transcriptions.transcripts.create(identifier, recording_id)
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
        path = tmp_path / f"{identifier}.ndjson"
        path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
        manager.transcriptions.transcripts.publish(
            transcript.id, "source", "config", path, 80, len(words)
        )
        return manager.transcriptions.transcripts.job(transcript.id)

    monkeypatch.setattr(YouTubeImports, "_process", process)
    monkeypatch.setattr(
        manager.media,
        "probe",
        lambda path: MediaInfo(control["duration"], 320, 180, True),
        raising=False,
    )
    monkeypatch.setattr(manager.transcriptions, "submit", transcribe)
    response = client.post("/api/youtube-sources", json={"url": URL})
    assert response.status_code == 201, response.text
    source = response.json()
    request = payload(recording)
    request.pop("recording_id")
    request["youtube_source_id"] = source["id"]
    yield client, manager, source, request, events, control, entered, release, provider, configured
    release.set()


def test_metadata_worker_never_downloads(tmp_path, monkeypatch):
    class MetadataDownloader:
        def __init__(self, options):
            assert options["remote_components"] == [] and options["plugin_dirs"] == []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def extract_info(self, url, **kwargs):
            assert kwargs == {"download": False, "ie_key": "Youtube"}
            return {
                "title": "Metadata only",
                "duration": 80,
                "formats": [format_info(360, combined=True)],
            }

        def dl(self, *args):
            pytest.fail("Metadata preview attempted a media download")

    monkeypatch.setattr(worker, "YouTubeDownloader", MetadataDownloader)
    result = worker.download(
        {"url": URL, "node": "node", "max_bytes": 100, "max_seconds": 100, "metadata_only": True},
        tmp_path,
    )
    assert result == {"name": "Metadata only", "duration": 80, "has_audio": True}
    assert list(tmp_path.iterdir()) == []


def test_preview_is_durable_without_processing(deferred):
    client, manager, source, _, events, _, _, _, provider, configured = deferred
    assert events == ["preview"] and not provider.calls
    assert len(client.get("/api/recordings").json()) == 1
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/youtube-sources/{source['id']}").json() == source
    reopened = YouTubeImports(manager.store, manager.media, configured)
    try:
        assert reopened.source(source["id"]).model_dump() == source
    finally:
        reopened.close()


def test_deferred_pipeline_progress_and_idempotency(deferred):
    client, manager, source, request, events, control, entered, release, provider, _ = deferred
    control["hold"] = True
    response = client.post("/api/projects", json=request)
    assert response.status_code == 202, response.text
    assert entered.wait(5)
    project = client.get(f"/api/projects/{request['id']}").json()
    assert project["status"] == "downloading" and project["recording_id"] is None
    job = manager.youtube.get(project["download_id"])
    job.downloaded_bytes, job.total_bytes = 40, 100
    manager.youtube.save(job)
    listed = client.get("/api/projects").json()[0]
    assert listed["progress"] == pytest.approx(0.1)
    assert "40%" in listed["stage"] and listed["source_thumbnail_url"].startswith(
        "https://i.ytimg.com/vi/"
    )
    assert client.post("/api/projects", json=request).status_code == 202
    release.set()
    result = finished(client, request["id"])
    assert result["status"] == "ready", result
    assert result["progress"] == 1 and result["recording_id"]
    assert events == ["preview", "download", "transcribe"] and len(provider.calls) == 1
    assert client.post("/api/projects", json=request).status_code == 202
    assert client.post("/api/projects", json={**request, "title": "Different"}).status_code == 409
    assert client.post(f"/api/projects/{request['id']}/resume", json={}).status_code == 202
    assert finished(client, request["id"])["status"] == "ready"
    assert events.count("download") == 1 and len(provider.calls) == 1


def test_cancel_owned_download_stops_before_transcription(deferred):
    client, _, _, request, events, control, entered, _, provider, _ = deferred
    control["hold"] = True
    assert client.post("/api/projects", json=request).status_code == 202
    assert entered.wait(5)
    assert client.post(f"/api/projects/{request['id']}/cancel").status_code == 200
    assert finished(client, request["id"])["status"] == "cancelled"
    assert "transcribe" not in events and not provider.calls


def test_failed_download_requires_explicit_resume(deferred):
    client, _, _, request, events, control, _, _, provider, _ = deferred
    control["fail"] = True
    assert client.post("/api/projects", json=request).status_code == 202
    result = finished(client, request["id"])
    assert result["status"] == "failed" and not provider.calls
    assert events.count("download") == 1
    control["fail"] = False
    assert client.post(f"/api/projects/{request['id']}/resume", json={}).status_code == 202
    assert finished(client, request["id"])["status"] == "ready"
    assert events.count("download") == 2


def test_missing_child_handoff_is_readable_and_recovers_explicitly(deferred):
    client, manager, source, request, events, _, _, _, _, _ = deferred
    project = Project(
        id=request["id"],
        youtube_source_id=source["id"],
        download_id=str(uuid4()),
        title=request["title"],
        created_at=now(),
        settings=ProjectSettings(**request["settings"]),
        status="downloading",
    )
    manager.records.save(project)
    response = client.get(f"/api/projects/{project.id}")
    assert response.status_code == 200 and response.json()["stage"] == "Starting source download"
    manager.records.initialize()
    assert client.get(f"/api/projects/{project.id}").json()["status"] == "interrupted"
    assert events == ["preview"]
    assert client.post(f"/api/projects/{project.id}/resume", json={}).status_code == 202
    assert finished(client, project.id)["status"] == "ready"


def test_cancelling_project_preserves_shared_download(deferred):
    client, manager, _, request, events, control, entered, release, provider, _ = deferred
    control["hold"] = True
    shared = manager.youtube.submit(YouTubeRequest(url=URL))
    assert entered.wait(5)
    assert client.post("/api/projects", json=request).status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        project = client.get(f"/api/projects/{request['id']}").json()
        if project["download_id"] == shared.id:
            break
        time.sleep(0.01)
    else:
        pytest.fail("Project did not attach to the shared download")
    assert client.post(f"/api/projects/{request['id']}/cancel").status_code == 200
    assert finished(client, request["id"])["status"] == "cancelled"
    assert manager.youtube.get(shared.id).status == "downloading"
    assert events.count("download") == 1 and not provider.calls
    release.set()


@pytest.mark.parametrize("duration,expected", [(79.8, "ready"), (60.0, "failed")])
def test_actual_duration_revalidated_with_metadata_rounding(deferred, duration, expected):
    client, _, _, request, _, control, _, _, provider, _ = deferred
    control["duration"] = duration
    request["settings"]["analysis_end"] = 80.0
    assert client.post("/api/projects", json=request).status_code == 202
    result = finished(client, request["id"])
    assert result["status"] == expected, result
    if expected == "ready":
        assert provider.calls[0][1].analysis_end == duration
    else:
        assert not provider.calls


def test_project_requires_exactly_one_valid_source(deferred):
    client, _, _, request, _, _, _, _, _, _ = deferred
    assert (
        client.post("/api/projects", json={**request, "recording_id": str(uuid4())}).status_code
        == 422
    )
    assert (
        client.post("/api/projects", json={**request, "youtube_source_id": None}).status_code == 422
    )
    assert (
        client.post(
            "/api/projects", json={**request, "youtube_source_id": str(uuid4())}
        ).status_code
        == 404
    )
    assert (
        client.post("/api/youtube-sources", json={"url": "https://127.0.0.1/video"}).status_code
        == 422
    )


def test_legacy_project_table_migration_preserves_records(context):
    _, manager, recording, _, _ = context
    project = Project(
        id=str(uuid4()),
        recording_id=recording.id,
        title="Existing project",
        created_at=now(),
        settings=ProjectSettings(analysis_end=65.0),
        status="ready",
    )
    manager.records.save(project)
    with manager.store.connection() as db:
        db.executescript(
            "CREATE TABLE legacy_projects (id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id), created_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL); INSERT INTO legacy_projects SELECT * FROM projects; DROP TABLE projects; ALTER TABLE legacy_projects RENAME TO projects;"
        )
    manager.records.initialize()
    assert manager.records.get(project.id) == project
    with manager.store.connection() as db:
        assert not next(
            row["notnull"]
            for row in db.execute("PRAGMA table_info(projects)")
            if row["name"] == "recording_id"
        )
        assert list(db.execute("PRAGMA foreign_key_check")) == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                (str(uuid4()), str(uuid4()), now(), "ready", "{}"),
            )
