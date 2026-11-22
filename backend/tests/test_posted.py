"""Manual posted markers persist independently of rendering and provider status."""

import json
from uuid import uuid4

import pytest

from backend.models import ExportRequest
from backend.project_store import ProjectStore
from backend.storage import Store
from backend.tests.test_batches import edit
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def test_project_marker_persists_and_updates_current_export_without_work(context):
    client, manager, recording, provider, configured = context
    project = create(client, payload(recording))
    item = project["items"][0]
    url = f"/api/projects/{project['id']}/clips/{item['id']}/posted"
    renders, calls = len(manager.media.renders), len(provider.calls)
    assert item["posted"] is False
    response = client.patch(url, json={"posted": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"posted": True}
    updated = client.get(f"/api/projects/{project['id']}").json()["items"][0]
    assert updated["posted"] is True and updated["revision"] == item["revision"]
    assert updated["export_id"] == item["export_id"] and updated["export"]["posted"] is True
    reopened = Store(configured.data_dir, initialize=False)
    assert ProjectStore(reopened).get(project["id"]).items[0].posted is True
    assert reopened.clip(item["export_id"]).posted is True
    assert any(c.posted and c.id == item["export_id"] for c in reopened.clips(recording.id))
    assert client.patch(url, json={"posted": True}).status_code == 200
    assert (len(manager.media.renders), len(provider.calls)) == (renders, calls)
    assert not manager.get(project["id"]).items[1].posted


def test_export_marker_updates_linked_project_and_supports_standalone_exports(context):
    client, manager, recording, _, _ = context
    project = create(client, payload(recording))
    item = project["items"][0]
    url = f"/api/clips/{item['export_id']}/posted"
    assert client.patch(url, json={"posted": True}).json()["posted"] is True
    assert manager.get(project["id"]).items[0].posted
    assert client.patch(url, json={"posted": False}).json()["posted"] is False
    assert not manager.get(project["id"]).items[0].posted
    standalone = manager.store.create_clip(
        str(uuid4()), recording.id, ExportRequest(start=0.0, end=2.0)
    )
    manager.store.update_clip(standalone.id, "succeeded")
    assert (
        client.patch(f"/api/clips/{standalone.id}/posted", json={"posted": True}).json()["posted"]
        is True
    )
    assert not any(i.posted for i in manager.get(project["id"]).items)


def test_marked_clip_stays_marked_after_edit_and_rerender(context):
    client, manager, recording, provider, _ = context
    project = create(client, payload(recording))
    item = project["items"][0]
    url = f"/api/projects/{project['id']}/clips/{item['id']}"
    assert client.patch(url + "/posted", json={"posted": True}).status_code == 200
    assert client.patch(url, json=edit(item, title="Updated marked clip")).status_code == 202
    updated = finished(client, project["id"])["items"][0]
    assert updated["export_id"] != item["export_id"]
    assert updated["posted"] and updated["export"]["posted"]
    assert manager.store.clip(item["export_id"]).posted
    assert len(provider.calls) == 1


def test_posted_validation_ownership_and_processing_guards(context):
    client, manager, recording, _, _ = context
    project = create(client, payload(recording))
    item = project["items"][0]
    urls = [
        f"/api/projects/{project['id']}/clips/{item['id']}/posted",
        f"/api/clips/{item['export_id']}/posted",
    ]
    for url in urls:
        for invalid in (
            {},
            {"posted": "true"},
            {"posted": 1},
            {"posted": None},
            {"posted": True, "title": "wrong"},
        ):
            assert client.patch(url, json=invalid).status_code == 422
        assert (
            client.patch(
                url, json={"posted": True}, headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
    assert (
        client.patch(
            f"/api/projects/{project['id']}/clips/{uuid4()}/posted", json={"posted": True}
        ).status_code
        == 404
    )
    assert client.patch(f"/api/clips/{uuid4()}/posted", json={"posted": True}).status_code == 404
    current = manager.get(project["id"], hydrate=False)
    current.status = "rendering"
    manager.records.save(current)
    for url in urls:
        assert client.patch(url, json={"posted": True}).status_code == 409
    assert not manager.store.clip(item["export_id"]).posted


def test_marker_write_failure_rolls_back_both_project_and_export(context, monkeypatch):
    client, manager, recording, _, _ = context
    project = create(client, payload(recording))
    item = project["items"][0]
    original = manager.store.set_posted

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("Synthetic storage failure")

    monkeypatch.setattr(manager.store, "set_posted", fail)
    with pytest.raises(OSError, match="Synthetic storage failure"):
        client.patch(
            f"/api/projects/{project['id']}/clips/{item['id']}/posted", json={"posted": True}
        )
    assert not manager.records.get(project["id"]).items[0].posted
    assert not manager.store.clip(item["export_id"]).posted


def test_legacy_projects_and_exports_default_to_unposted(context):
    client, manager, recording, _, _ = context
    project = create(client, payload(recording))
    with manager.store.connection() as db:
        stored = json.loads(
            db.execute("SELECT payload FROM projects WHERE id=?", (project["id"],)).fetchone()[0]
        )
        for item in stored["items"]:
            item.pop("posted")
        db.execute("UPDATE projects SET payload=? WHERE id=?", (json.dumps(stored), project["id"]))
    assert all(not i.posted for i in manager.records.get(project["id"]).items)
    assert all(not c.posted for c in manager.store.clips(recording.id))


def test_new_sibling_highlights_start_unposted(context, monkeypatch):
    from backend.highlight_models import HighlightError
    from backend.tests.test_multi_presentation import choices, request

    client, manager, recording, _, _ = context
    calls = []

    async def review(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise HighlightError("Synthetic interrupted review")
        return choices(), 10, 5

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    project = create(client, request(recording))
    first = project["items"][0]
    assert (
        client.patch(
            f"/api/projects/{project['id']}/clips/{first['id']}/posted", json={"posted": True}
        ).status_code
        == 200
    )
    client.post(f"/api/projects/{project['id']}/resume", json={"retry_analysis": True})
    result = finished(client, project["id"])
    assert result["status"] == "ready" and len(result["items"]) == 2
    assert result["items"][0]["posted"] and result["items"][0]["export"]["posted"]
    assert not result["items"][1]["posted"] and not result["items"][1]["export"]["posted"]
