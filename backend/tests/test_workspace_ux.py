"""Workspace persistence and mutation boundaries with explicit synthetic adapters."""

from uuid import uuid4

from backend.project_models import ItemEdit, Project
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools


def targets(project):
    return [{"id": item["id"], "revision": item["revision"]} for item in project["items"]]


def edit(item, **changes):
    return {
        "auto_presentation": False,
        **{key: item[key] for key in ItemEdit.model_fields if key != "auto_presentation"},
        **changes,
    }


def test_global_search_filters_sort_and_pagination(context):
    client, manager, recording, _, _ = context
    original = create(client, payload(recording))
    model = Project.model_validate(original)
    for index in range(55):
        manager.records.save(
            model.model_copy(
                update={
                    "id": str(uuid4()),
                    "title": f"Workspace {index:02}",
                    "status": "failed" if index % 2 else "ready",
                    "items": [],
                }
            )
        )
    found = client.get(
        "/api/projects", params={"search": "Workspace 01", "status": "failed"}
    ).json()
    assert len(found) == 1 and found[0]["title"] == "Workspace 01"
    assert client.get("/api/projects", params={"search": "%' OR 1=1 --"}).json() == []
    ordered = client.get(
        "/api/projects", params={"search": "Workspace", "sort": "title", "offset": 50}
    ).json()
    assert [item["title"] for item in ordered] == [f"Workspace {i}" for i in range(50, 55)]
    assert client.get("/api/projects?sort=invalid").status_code == 422


def test_presets_and_draft_validation_never_restore_consent(context):
    client, manager, recording, _, _ = context
    settings = payload(recording)["settings"]
    preset = {
        "name": "Gaming campaign",
        "workflow": "long",
        "settings": {
            **settings,
            "ai_instructions": "Keep the whole setup and payoff",
            "headline_position": "centre",
        },
    }
    response = client.post("/api/project-presets", json=preset)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert client.post("/api/project-presets", json=preset).status_code == 409
    assert client.get("/api/project-presets").json()[0] == saved
    with manager.store.connection() as db:
        assert db.execute("SELECT count(*) FROM project_presets").fetchone()[0] == 1
    assert (
        client.post("/api/project-presets", json={**preset, "remote_consent": True}).status_code
        == 422
    )
    assert (
        client.post("/api/project-presets", json={**preset, "workflow": "batch"}).status_code == 422
    )
    draft = {
        "title": "",
        "settings": settings,
        "recording_ids": [],
        "creation_token": str(uuid4()),
        "creation_signature": "0" * 64,
    }
    assert client.post("/api/setup/validate", json=draft).status_code == 200
    assert (
        client.post("/api/setup/validate", json={**draft, "remote_consent": True}).status_code
        == 422
    )
    assert client.delete(f"/api/project-presets/{saved['id']}").status_code == 200
    assert client.get("/api/project-presets").json() == []


def test_bulk_revision_validation_and_posted_are_atomic(context):
    client, manager, recording, provider, _ = context
    project = create(client, payload(recording))
    path = f"/api/projects/{project['id']}/bulk"
    stale = targets(project)
    stale[-1]["revision"] += 1
    assert (
        client.post(path, json={"targets": stale, "action": "posted", "posted": True}).status_code
        == 409
    )
    assert not any(
        item["posted"] for item in client.get(f"/api/projects/{project['id']}").json()["items"]
    )
    posted = client.post(
        path, json={"targets": targets(project), "action": "posted", "posted": True}
    )
    assert posted.status_code == 202, posted.text
    value = posted.json()
    assert all(
        item["posted"] and manager.store.clip(item["export_id"]).posted for item in value["items"]
    )
    accepted = client.post(
        path, json={"targets": targets(value), "action": "review", "review": "accepted"}
    )
    assert accepted.status_code == 202, accepted.text
    assert all(item["review"] == "accepted" for item in accepted.json()["items"])
    assert len(manager.media.renders) == 2 and len(provider.calls) == 1
    assert (
        client.post(
            path, json={"targets": [targets(value)[0]] * 2, "action": "posted", "posted": False}
        ).status_code
        == 422
    )


def test_bulk_headline_collision_rolls_back_every_target(context):
    client, manager, recording, _, _ = context
    value = create(client, payload(recording))
    stored = manager.records.get(value["id"])
    from backend.overlays import TextOverlay

    stored.items[-1].overlays = [
        TextOverlay(text="Existing overlay", start=1.0, end=3.0, position="centre")
    ]
    manager.records.save(stored)
    response = client.post(
        f"/api/projects/{value['id']}/bulk",
        json={
            "targets": targets(value),
            "action": "headline",
            "headline_enabled": True,
            "headline_position": "centre",
        },
    )
    assert response.status_code == 422
    after = manager.records.get(value["id"])
    assert [item.revision for item in after.items] == [0, 0]
    assert not manager.records.versions(after.id, after.items[0].id)


def test_restore_reuses_original_export_and_keeps_posted_separate(context):
    client, manager, recording, provider, _ = context
    value = create(client, payload(recording))
    item = value["items"][0]
    original_export = item["export_id"]
    route = f"/api/projects/{value['id']}/clips/{item['id']}"
    changed = client.patch(route, json=edit(item, title="A different headline"))
    assert changed.status_code == 202, changed.text
    value = finished(client, value["id"])
    versions = client.get(f"{route}/versions").json()
    assert (
        len(versions) == 1
        and versions[0]["export_id"] == original_export
        and versions[0]["playable"]
    )
    item = value["items"][0]
    posted = client.post(
        f"/api/projects/{value['id']}/bulk",
        json={
            "targets": [{"id": item["id"], "revision": item["revision"]}],
            "action": "posted",
            "posted": True,
        },
    ).json()
    current = posted["items"][0]
    rendered = len(manager.media.renders)
    restored = client.post(
        f"{route}/restore", json={"id": versions[0]["id"], "revision": current["revision"]}
    )
    assert restored.status_code == 202, restored.text
    result = finished(client, value["id"])
    assert result["items"][0]["export_id"] == original_export
    assert result["items"][0]["posted"] and manager.store.clip(original_export).posted
    assert len(manager.media.renders) == rendered and len(provider.calls) == 1
    assert (
        client.post(
            f"{route}/restore", json={"id": versions[0]["id"], "revision": current["revision"]}
        ).status_code
        == 409
    )
    other_route = f"/api/projects/{value['id']}/clips/{value['items'][1]['id']}/restore"
    assert (
        client.post(other_route, json={"id": versions[0]["id"], "revision": 0}).status_code == 404
    )
    assert client.get(f"{route}/versions").json()[0]["settings"]["title"] == "A different headline"


def test_saved_project_headline_preferences_are_applied_to_items(context):
    client, _, recording, _, _ = context
    project = create(client, payload(recording, headline_enabled=False, headline_position="centre"))
    assert all(
        not item["headline_enabled"] and item["headline_position"] == "centre"
        for item in project["items"]
    )
    from backend.tests.test_batches import batch

    supplied = create(
        client, batch([recording], headline_enabled=False, headline_position="centre")
    )
    assert all(
        not item["headline_enabled"] and item["headline_position"] == "centre"
        for item in supplied["items"]
    )


def test_version_history_is_bounded_and_removed_with_project(context):
    client, manager, recording, _, _ = context
    value = create(client, payload(recording))
    project = manager.records.get(value["id"])
    with manager.store.connection() as db:
        for _ in range(55):
            manager.records.remember_version(project, project.items[0].id, db)
    assert len(manager.records.versions(project.id, project.items[0].id)) == 50
    response = client.delete(f"/api/projects/{project.id}")
    assert response.status_code == 200, response.text
    with manager.store.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM project_item_versions WHERE project_id=?", (project.id,)
            ).fetchone()[0]
            == 0
        )


def test_bulk_updates_are_blocked_while_processing(context):
    client, manager, recording, _, _ = context
    value = create(client, payload(recording))
    project = manager.records.get(value["id"])
    project.status = "rendering"
    manager.records.save(project)
    response = client.post(
        f"/api/projects/{project.id}/bulk",
        json={"targets": targets(value), "action": "posted", "posted": True},
    )
    assert response.status_code == 409
    assert not any(item.posted for item in manager.records.get(project.id).items)


def test_unfinished_range_can_be_recovered_but_cannot_start_processing(context):
    client, manager, recording, provider, _ = context
    unfinished = {
        "analysis_start": 10.0,
        "analysis_end": 11.0,
        "duration_mode": "manual",
        "min_seconds": 30,
        "max_seconds": 20,
    }
    recovered = client.post("/api/setup/validate", json={"title": "Draft", "settings": unfinished})
    assert recovered.status_code == 200
    assert recovered.json()["settings"]["min_seconds"] == 30
    assert (
        client.post(
            "/api/projects",
            json={
                "id": str(uuid4()),
                "recording_id": recording.id,
                "title": "Draft",
                "settings": unfinished,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/project-presets",
            json={"name": "Unfinished", "workflow": "long", "settings": unfinished},
        ).status_code
        == 422
    )
    assert manager.records.list() == [] and provider.calls == []
