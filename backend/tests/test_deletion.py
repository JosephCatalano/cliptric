"""Deletion contracts use isolated synthetic media and no external requests."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from backend.deletion import Deletions
from backend.models import ExportRequest
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def post_row(manager, table, export_id, status, *, identifier=None):
    identifier = identifier or str(uuid4())
    with manager.store.connection() as db:
        if table == "scheduled_posts":
            db.execute(
                "INSERT INTO scheduled_posts (id,platform,export_id,account_id,scheduled_at,status,payload,sha256) VALUES (?, 'tiktok', ?, 'account', '2099-01-01T00:00:00+00:00', ?, '{}', 'digest')",
                (identifier, export_id, status),
            )
        else:
            db.execute(
                f"INSERT INTO {table} VALUES (?, ?, 'account', ?, '2026-11-21', '{{}}')",
                (identifier, export_id, status),
            )
    return identifier


def test_delete_project_removes_current_exports_and_preserves_sources_history(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    previous = manager.store.create_clip(
        str(uuid4()), recording.id, ExportRequest(start=0.0, end=3.0)
    )
    manager.store.update_clip(previous.id, "succeeded")
    manager.store.export_path(previous.id).write_bytes(b"older-version")
    source = manager.store.source_path(recording.id)
    before = source.read_bytes()
    paths = [manager.store.export_path(i["export_id"]) for i in result["items"]]
    thumbs = list(manager.root.glob("*.jpg"))
    response = client.delete(f"/api/projects/{result['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "cleanup_pending": False}
    assert client.get(f"/api/projects/{result['id']}").status_code == 404
    assert client.get("/api/projects").json() == []
    assert not any(p.exists() for p in paths + thumbs)
    assert source.read_bytes() == before
    assert manager.transcriptions.latest(recording.id).status == "succeeded"
    assert manager.store.clip(previous.id) and manager.store.export_path(previous.id).is_file()
    for item in result["items"]:
        assert client.get(f"/api/clips/{item['export_id']}/media").status_code == 404
        for table in ("clip_captions", "clip_headlines", "clip_quality"):
            with manager.store.connection() as db:
                assert not db.execute(
                    f"SELECT 1 FROM {table} WHERE clip_id=?", (item["export_id"],)
                ).fetchone()
    assert len(provider.calls) == 1


def test_delete_project_item_checks_membership_revision_and_prevents_regeneration(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    item = result["items"][0]
    url = f"/api/projects/{result['id']}/clips/{item['id']}"
    assert client.delete(url).status_code == 422
    assert client.delete(url + "?revision=99").status_code == 409
    assert (
        client.delete(f"/api/projects/{result['id']}/clips/{uuid4()}?revision=0").status_code == 404
    )
    assert manager.store.export_path(item["export_id"]).is_file()
    response = client.delete(url + f"?revision={item['revision']}")
    assert response.status_code == 200, response.text
    assert not manager.store.export_path(item["export_id"]).exists()
    project = manager.get(result["id"])
    assert [i.id for i in project.items] == [result["items"][1]["id"]]
    renders = len(manager.media.renders)
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert len(resumed["items"]) == 1 and len(manager.media.renders) == renders
    assert len(provider.calls) == 1


def test_studio_delete_removes_linked_project_entry(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    identifier = result["items"][0]["export_id"]
    assert client.delete(f"/api/clips/{identifier}").status_code == 200
    assert manager.store.clip(identifier) is None
    assert len(manager.get(result["id"]).items) == 1
    assert client.delete(f"/api/clips/{identifier}").status_code == 404
    assert client.delete("/api/clips/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    "table,status",
    [
        ("scheduled_posts", "scheduled"),
        ("scheduled_posts", "dispatching"),
        ("scheduled_posts", "unknown"),
        ("tiktok_posts", "uploading"),
        ("tiktok_posts", "unknown"),
        ("youtube_uploads", "paused"),
        ("instagram_posts", "publishing"),
        ("x_posts", "uploading"),
        ("facebook_posts", "unknown"),
    ],
)
def test_project_deletion_rolls_back_when_any_export_is_in_use(context, table, status):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    identifier = result["items"][-1]["export_id"]
    post_row(manager, table, identifier, status)
    response = client.delete(f"/api/projects/{result['id']}")
    assert response.status_code == 409, response.text
    assert len(manager.get(result["id"]).items) == 2
    assert all(manager.store.export_path(i["export_id"]).exists() for i in result["items"])
    with manager.store.connection() as db:
        assert not db.execute("SELECT 1 FROM deleted_files").fetchone()


def test_completed_social_posts_remain_after_local_export_deletion(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    identifier = result["items"][0]["export_id"]
    job_id = post_row(manager, "tiktok_posts", identifier, "published")
    post_row(manager, "scheduled_posts", identifier, "submitted", identifier=job_id)
    post_row(manager, "youtube_uploads", identifier, "ready")
    post_row(manager, "instagram_posts", identifier, "published")
    x_id = post_row(manager, "x_posts", identifier, "published")
    facebook_id = post_row(manager, "facebook_posts", identifier, "published")
    assert client.delete(f"/api/clips/{identifier}").status_code == 200
    with manager.store.connection() as db:
        assert (
            db.execute("SELECT status FROM tiktok_posts WHERE id=?", (job_id,)).fetchone()[0]
            == "published"
        )
        assert (
            db.execute("SELECT status FROM x_posts WHERE id=?", (x_id,)).fetchone()[0]
            == "published"
        )
        assert (
            db.execute("SELECT status FROM facebook_posts WHERE id=?", (facebook_id,)).fetchone()[0]
            == "published"
        )


def test_processing_guards_preserve_files(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    project = manager.get(result["id"], hydrate=False)
    project.status = "rendering"
    manager.records.save(project)
    assert client.delete(f"/api/projects/{project.id}").status_code == 409
    identifier = project.items[0].export_id
    assert client.delete(f"/api/clips/{identifier}").status_code == 409
    project.status = "ready"
    manager.records.save(project)
    with manager.jobs.lock:
        manager.jobs.active = identifier
    try:
        assert client.delete(f"/api/clips/{identifier}").status_code == 409
    finally:
        with manager.jobs.lock:
            manager.jobs.active = None
    assert manager.store.export_path(identifier).exists()


def test_locked_file_cleanup_is_durable_and_retried(context, monkeypatch):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    identifier = result["items"][0]["export_id"]
    video = manager.store.export_path(identifier)
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == video:
            raise PermissionError("Synthetic Windows file lock")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    response = client.delete(f"/api/clips/{identifier}")
    assert response.status_code == 200 and response.json()["cleanup_pending"]
    assert manager.store.clip(identifier) is None and video.exists()
    with manager.store.connection() as db:
        assert (
            db.execute("SELECT path FROM deleted_files").fetchone()[0]
            == f"exports/{identifier}.mp4"
        )
    monkeypatch.setattr(Path, "unlink", unlink)
    service = Deletions(
        manager.store, manager, manager.jobs, type("Scheduler", (), {"lock": manager.lock})(), []
    )
    service.initialize()
    assert not video.exists()
    with manager.store.connection() as db:
        assert not db.execute("SELECT 1 FROM deleted_files").fetchone()


def test_cross_origin_delete_is_rejected(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    response = client.delete(
        f"/api/projects/{result['id']}", headers={"Origin": "https://untrusted.example"}
    )
    assert response.status_code == 403
    assert manager.get(result["id"])


def test_busy_service_returns_conflict_without_waiting_or_deleting(context):
    client, manager, recording, _, _ = context
    result = create(client, payload(recording))
    entered, release = threading.Event(), threading.Event()

    def hold_lock():
        with client.app.state.tiktok.lock:
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(hold_lock)
        assert entered.wait(2)
        try:
            response = client.delete(f"/api/projects/{result['id']}")
            assert response.status_code == 409
            assert "busy" in response.json()["detail"]
        finally:
            release.set()
        task.result()
    assert manager.get(result["id"])
