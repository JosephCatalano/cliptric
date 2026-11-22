"""Manual framing validation, durable snapshots, and real encoded crop geometry."""

import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.framing import SourceRegion
from backend.media import Media
from backend.models import ExportRequest, SelectionDraft
from backend.presentation import PresentationPlan
from backend.storage import Store
from backend.tests.test_batches import batch
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools
from backend.tests.test_split_framing import pixel, rgb
from backend.tests.test_split_framing import stream_source as stream_source
from backend.tests.test_workspace_ux import edit

CROP = {"x": 0.75, "y": 0.0, "width": 0.25, "height": 0.24}


@pytest.mark.parametrize("mode", ["pad", "split"])
def test_crop_requires_fill_layout(mode):
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, mode=mode, crop_region=CROP)


@pytest.mark.parametrize("changes", [{"x": True}, {"height": float("nan")}, {"x": 0.9}])
def test_crop_rejects_invalid_coordinates(changes):
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, mode="crop", crop_region={**CROP, **changes})


def test_manual_crop_draft_and_export_snapshots_reopen(context):
    _, manager, recording, _, configured = context
    request = ExportRequest(start=0, end=1, mode="crop", crop_region=CROP)
    clip = manager.store.create_clip(str(uuid4()), recording.id, request)
    manager.store.save_draft(
        recording.id, SelectionDraft(start=0, end=1, mode="crop", crop_region=CROP)
    )
    reopened = Store(configured.data_dir)
    assert reopened.clip(clip.id).crop_region.model_dump() == CROP
    assert reopened.clips(recording.id)[0].crop_region.model_dump() == CROP
    assert reopened.draft(recording).crop_region.model_dump() == CROP


def test_project_crop_rerenders_only_changed_clip_and_restore_reuses_export(context):
    client, manager, recording, provider, _ = context
    project = create(client, payload(recording, framing="crop", crop_region=CROP))
    first, other = project["items"]
    original = first["export_id"]
    assert first["export"]["crop_region"] == CROP
    route = f"/api/projects/{project['id']}/clips/{first['id']}"
    replacement = {**CROP, "x": 0.0}
    renders, calls = len(manager.media.renders), len(provider.calls)
    response = client.patch(route, json=edit(first, crop_region=replacement))
    assert response.status_code == 202, response.text
    changed = finished(client, project["id"])
    assert changed["items"][0]["export"]["crop_region"] == replacement
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clip(original).crop_region.model_dump() == CROP
    assert len(manager.media.renders) == renders + 1 and len(provider.calls) == calls
    versions = client.get(route + "/versions").json()
    response = client.post(
        route + "/restore",
        json={"id": versions[0]["id"], "revision": changed["items"][0]["revision"]},
    )
    assert response.status_code == 202, response.text
    restored = finished(client, project["id"])
    assert restored["items"][0]["export_id"] == original
    assert len(manager.media.renders) == renders + 1


def test_batch_inherits_crop_and_presentation_preserves_manual_region(context, monkeypatch):
    client, manager, recording, _, _ = context
    monkeypatch.setattr(
        manager.presentation,
        "analyze",
        lambda *args: PresentationPlan(summary="Synthetic presentation"),
    )
    project = create(
        client,
        batch([recording], framing="crop", crop_region=CROP, presentation_mode="auto"),
    )
    assert project["status"] == "ready", project
    assert project["items"][0]["export"]["crop_region"] == CROP
    assert project["items"][0]["export"]["mode"] == "crop"


def test_actual_crop_and_cover_use_same_selected_picture(settings, stream_source, tmp_path):
    media = Media(settings)
    cover = tmp_path / "cover.jpg"
    cancel = threading.Event()
    media.thumbnail(stream_source, cover, 1.5, cancel)
    output = tmp_path / "manual.mp4"
    info = media.render(
        stream_source,
        output,
        0,
        2,
        "crop",
        cancel,
        tmp_path / "crop.log",
        crop_region=SourceRegion.model_validate(CROP),
        cover_path=cover,
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    assert info.duration == pytest.approx(2, abs=0.1)
    # Source top-right changes red to green at one second. The independent
    # opening cover is green. No blue gameplay should leak into either crop.
    for seconds, channel in [(0.1, 1), (0.5, 0), (1.5, 1)]:
        frame = rgb(settings, output, seconds)
        for x, y in [(20, 20), (540, 960), (1050, 1880)]:
            colour = pixel(frame, x, y)
            assert colour[channel] > 180 and colour[2] < 50, (seconds, colour)
