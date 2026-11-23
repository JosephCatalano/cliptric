"""Repeat VOD batches use explicit synthetic AI/media; no provider calls or ASR."""

import asyncio
from uuid import uuid4

import pytest

from backend.highlight_models import ProposedHighlights, ProviderResult
from backend.project_store import ProjectStore
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def install_provider(provider, monkeypatch):
    async def suggest(window, request, *, context=None):
        provider.calls.append((window, request))
        provider.entered.set()
        while provider.hold:
            await asyncio.sleep(0.01)
        return ProviderResult(
            ProposedHighlights(
                candidates=[
                    dict(
                        first_segment=window[index].id,
                        last_segment=window[index + 1].id,
                        title=f"Moment {window[index].id}",
                        reason="Explicit synthetic passage",
                        score=90 - index,
                        evidence=window[index].text,
                        category="story",
                    )
                    for index in range(0, min(len(window) - 1, request.max_candidates * 2), 2)
                ]
            ),
            100,
            40,
        )

    monkeypatch.setattr(provider, "suggest", suggest)


def more(client, project_id, *, count=3, token=None):
    request = {"id": token or str(uuid4()), "max_clips": count, "remote_consent": True}
    response = client.post(f"/api/projects/{project_id}/more-clips", json=request)
    assert response.status_code == 202, response.text
    return finished(client, response.json()["id"])


def ranges(project):
    return [(item["start"], item["end"]) for item in project["items"]]


def test_three_then_three_after_reopen_deletion_and_exhaustion(context, monkeypatch):
    client, manager, recording, provider, _ = context
    install_provider(provider, monkeypatch)
    first = create(client, payload(recording, analysis_start=0.0, analysis_end=80.0, max_clips=3))
    assert first["status"] == "ready" and len(first["items"]) == 3
    original_files = [manager.store.export_path(item["export_id"]) for item in first["items"]]
    ProjectStore(manager.store).initialize()
    second = more(client, first["id"])
    assert second["status"] == "ready" and len(second["items"]) == 3
    assert second["recording_id"] == first["recording_id"] == recording.id
    assert second["transcription_id"] == first["transcription_id"]
    assert second["settings"] == first["settings"]
    assert second["parent_project_id"] == first["id"] and second["avoid_used_clips"]
    assert client.get(f"/api/projects/{first['id']}").json()["items"] == first["items"]
    assert all(path.is_file() for path in original_files)

    assert client.delete(f"/api/projects/{second['id']}").status_code == 200
    ProjectStore(manager.store).initialize()
    third = more(client, first["id"])
    assert third["status"] == "ready" and len(third["items"]) == 2
    all_ranges = ranges(first) + ranges(second) + ranges(third)
    assert len(set(all_ranges)) == 8
    for index, (start, end) in enumerate(all_ranges):
        assert all(
            end <= other_start or start >= other_end
            for other_start, other_end in all_ranges[index + 1 :]
        )
    calls = len(provider.calls)
    exhausted = more(client, third["id"])
    assert exhausted["status"] == "ready" and exhausted["items"] == []
    assert "unused moments" in exhausted["stage"]
    assert len(provider.calls) == calls  # No API request when there is no usable speech left.
    for window, request in provider.calls:
        assert all(
            segment.end <= old.start or segment.start >= old.end
            for segment in window
            for old in request.excluded_ranges
        )


def test_idempotent_batch_count_conflict_and_small_limit(context, monkeypatch):
    client, _, recording, provider, _ = context
    install_provider(provider, monkeypatch)
    first = create(client, payload(recording, analysis_start=0.0, analysis_end=80.0, max_clips=3))
    token = str(uuid4())
    second = more(client, first["id"], count=1, token=token)
    assert len(second["items"]) == 1
    calls = len(provider.calls)
    assert more(client, first["id"], count=1, token=token)["id"] == second["id"]
    assert len(provider.calls) == calls
    assert (
        client.post(
            f"/api/projects/{first['id']}/more-clips",
            json={"id": token, "max_clips": 3, "remote_consent": True},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"remote_consent": False},
        {"remote_consent": "true"},
        {"remote_consent": 1},
        {"max_clips": 0},
        {"max_clips": 21},
        {"max_clips": "3"},
        {"extra": True},
    ],
)
def test_more_request_validation(context, changes):
    client, _, _, provider, _ = context
    response = client.post(
        f"/api/projects/{uuid4()}/more-clips",
        json={"id": str(uuid4()), "max_clips": 3, "remote_consent": True, **changes},
    )
    assert response.status_code == 422 and not provider.calls


def test_history_backfills_existing_projects_and_is_source_specific(context):
    client, manager, recording, _, _ = context
    first = create(client, payload(recording))
    with manager.store.connection() as db:
        db.execute("DROP TABLE project_selections")
    records = ProjectStore(manager.store)
    records.initialize()
    assert [
        (section.start, section.end) for section in records.used_ranges(recording.id)
    ] == ranges(first)
    assert records.used_ranges(str(uuid4())) == []
    deleted = first["items"][0]
    assert (
        client.delete(f"/api/projects/{first['id']}/clips/{deleted['id']}?revision=0").status_code
        == 200
    )
    records.initialize()
    assert [
        (section.start, section.end) for section in records.used_ranges(recording.id)
    ] == ranges(first)


def test_failed_batch_retry_retains_exclusions(context, monkeypatch):
    client, manager, recording, provider, _ = context
    install_provider(provider, monkeypatch)
    first = create(client, payload(recording, analysis_start=0.0, analysis_end=80.0, max_clips=3))
    manager.media.fail_start = 30.0
    second = more(client, first["id"])
    assert second["status"] == "partial"
    selected = ranges(second)
    calls = len(provider.calls)
    manager.media.fail_start = None
    assert client.post(f"/api/projects/{second['id']}/resume", json={}).status_code == 202
    resumed = finished(client, second["id"])
    assert resumed["status"] == "ready" and ranges(resumed) == selected
    assert len(provider.calls) == calls


def test_queued_batches_snapshot_history_when_they_start(context, monkeypatch):
    client, _, recording, provider, _ = context
    install_provider(provider, monkeypatch)
    first = create(client, payload(recording, analysis_start=0.0, analysis_end=80.0, max_clips=3))
    provider.entered.clear()
    provider.hold = True
    try:
        one = client.post(
            f"/api/projects/{first['id']}/more-clips",
            json={"id": str(uuid4()), "max_clips": 3, "remote_consent": True},
        )
        assert one.status_code == 202 and provider.entered.wait(5)
        two = client.post(
            f"/api/projects/{first['id']}/more-clips",
            json={"id": str(uuid4()), "max_clips": 3, "remote_consent": True},
        )
        assert two.status_code == 202
    finally:
        provider.hold = False
    second = finished(client, one.json()["id"])
    third = finished(client, two.json()["id"])
    assert len(second["items"]) == 3 and len(third["items"]) == 2
    assert all(
        end <= other_start or start >= other_end
        for start, end in ranges(second)
        for other_start, other_end in ranges(third)
    )
