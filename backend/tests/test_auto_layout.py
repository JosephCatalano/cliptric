"""Adaptive layout decisions, immutable reuse, and real composed output."""

import threading

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

import backend.auto_layout as auto_layout_module
from backend.auto_layout import camera_panel, choose_layout
from backend.follow_models import FollowPlan, follow_fit
from backend.framing import SourceRegion, SplitLayout
from backend.layout_models import AutoLayoutPlan
from backend.media import Media, RenderCancelled
from backend.models import ExportRequest
from backend.storage import Store
from backend.tests.test_batches import batch
from backend.tests.test_pipeline import await_export
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools
from backend.tests.test_split_framing import pixel, rgb
from backend.tests.test_split_framing import stream_source as stream_source
from backend.tests.test_workspace_ux import edit


def inset(background=30, offset=0):
    frame = np.full((216, 384, 3), background, np.uint8)
    cv2.rectangle(frame, (10 + offset, 5), (102 + offset, 80), (100, 100, 100), -1)
    cv2.rectangle(frame, (10 + offset, 5), (102 + offset, 80), (255, 255, 255), 2)
    return frame


def test_rectangular_webcam_with_static_context_focuses_face():
    frame = inset()
    face = (0.10, 0.08, 0.08, 0.12)
    assert camera_panel(frame, face) is not None
    result = choose_layout([(0, frame), (1, frame)], [[face], [face]], 1920, 1080, 0, 2)
    assert result.mode == "crop", result
    assert result.crop_region.x <= face[0]
    assert result.crop_region.x + result.crop_region.width >= face[0] + face[2]


def test_webcam_and_active_background_choose_split_preserving_context():
    face = (0.10, 0.08, 0.08, 0.12)
    result = choose_layout([(0, inset()), (1, inset(140))], [[face], [face]], 1920, 1080, 0, 2)
    assert result.mode == "split", result
    assert result.split_layout.gameplay == SourceRegion(x=0, y=0, width=1, height=1)
    assert result.split_layout.gameplay_fit == "fit" and result.warnings


def test_broken_contours_need_visible_panel_edges_in_every_sample(monkeypatch):
    original = camera_panel
    count = 0

    def intermittent(frame, face):
        nonlocal count
        count += 1
        return original(frame, face) if count % 2 else None

    monkeypatch.setattr(auto_layout_module, "camera_panel", intermittent)
    frames = [inset(), inset(140), inset(), inset(140)]
    face = [(0.1, 0.08, 0.08, 0.12)]
    result = choose_layout(list(enumerate(frames)), [face] * 4, 1920, 1080, 0, 4)
    assert result.mode == "split", result
    frames[1] = np.full_like(frames[1], 140)
    count = 0
    result = choose_layout(list(enumerate(frames)), [face] * 4, 1920, 1080, 0, 4)
    assert result.mode == "pad" and "boundary" in result.summary


def test_clear_face_chooses_static_crop_or_follow_and_ambiguous_picture_fits():
    frame = np.full((216, 384, 3), 90, np.uint8)
    samples = [(0, frame), (1, frame)]
    fixed = [[(0.4, 0.2, 0.2, 0.3)]] * 2
    assert choose_layout(samples, fixed, 1920, 1080, 0, 2).mode == "crop"
    moving = [fixed[0], [(0.5, 0.2, 0.2, 0.3)]]
    assert choose_layout(samples, moving, 1920, 1080, 0, 2).mode == "follow"
    assert choose_layout(samples, [fixed[0] * 2] * 2, 1920, 1080, 0, 2).mode == "pad"
    assert choose_layout(samples, [[(0.1, 0.1, 0.08, 0.12)]] * 2, 1920, 1080, 0, 2).mode == "pad"
    assert (
        choose_layout(
            [(0, inset()), (1, inset(30, 30))],
            [[(0.1, 0.08, 0.08, 0.12)], [(0.18, 0.08, 0.08, 0.12)]],
            1920,
            1080,
            0,
            2,
        ).mode
        == "pad"
    )


def test_auto_rejects_competing_regions_and_forged_decision():
    with pytest.raises(ValidationError):
        ExportRequest(
            start=0, end=1, mode="auto", crop_region={"x": 0, "y": 0, "width": 1, "height": 1}
        )
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, mode="auto", auto_layout={"mode": "crop"})
    with pytest.raises(ValidationError):
        AutoLayoutPlan(start=0, end=1, mode="crop", summary="Missing region")


def test_auto_project_decisions_reopen_reuse_and_manual_override(context, monkeypatch):
    client, manager, recording, provider, configured = context
    calls = []

    def analyze(source, recording, start, end, cancel):
        calls.append((start, end))
        return AutoLayoutPlan(
            start=start,
            end=end,
            mode="crop",
            crop_region=SourceRegion(x=0.3, y=0, width=0.3, height=1),
            summary="Synthetic face focus",
        )

    monkeypatch.setattr(manager.jobs.layout, "analyze", analyze)
    result = create(client, payload(recording, framing="auto"))
    assert result["status"] == "ready", result
    item = result["items"][0]
    original = item["export_id"]
    assert item["export"]["auto_layout"]["mode"] == "crop" and len(calls) == 2
    route = f"/api/projects/{result['id']}/clips/{item['id']}"
    response = client.patch(route, json=edit(item, title="Different title"))
    assert response.status_code == 202, response.text
    result = finished(client, result["id"])
    assert len(calls) == 2 and len(provider.calls) == 1
    reopened = Store(configured.data_dir)
    assert reopened.clip(original).auto_layout.summary == "Synthetic face focus"
    assert reopened.clips(recording.id)[0].auto_layout is not None
    item = result["items"][0]
    response = client.patch(route, json=edit(item, framing="pad"))
    assert response.status_code == 202, response.text
    result = finished(client, result["id"])
    assert result["items"][0]["export"]["auto_layout"] is None
    assert len(calls) == 2


def test_auto_follow_fallback_records_actual_full_picture_choice(context, monkeypatch):
    client, manager, recording, _, _ = context
    monkeypatch.setattr(
        manager.jobs.layout,
        "analyze",
        lambda source, recording, start, end, cancel: AutoLayoutPlan(
            start=start, end=end, mode="follow", summary="Synthetic follow choice"
        ),
    )
    monkeypatch.setattr(
        manager.jobs.follow,
        "analyze",
        lambda source, start, end, *args: follow_fit(start, end, "Synthetic uncertain tracking"),
    )
    result = create(client, batch([recording], framing="auto"))
    assert result["status"] == "ready", result
    assert result["items"][0]["export"]["auto_layout"]["mode"] == "pad"
    assert result["items"][0]["export"]["auto_layout"]["warnings"] == [
        "Synthetic uncertain tracking"
    ]


def test_auto_split_keeps_automatic_text_open_until_composition(context, monkeypatch):
    from backend.presentation import PresentationPlan

    client, manager, recording, _, _ = context
    calls = []

    def analyze(source, recording, start, end, cancel):
        calls.append(True)
        return AutoLayoutPlan(
            start=start,
            end=end,
            mode="split",
            summary="Synthetic inset",
            split_layout=SplitLayout(
                webcam=SourceRegion(x=0.75, y=0, width=0.25, height=0.25),
                gameplay=SourceRegion(x=0, y=0, width=1, height=1),
            ),
        )

    monkeypatch.setattr(manager.jobs.layout, "analyze", analyze)
    monkeypatch.setattr(
        manager.presentation,
        "analyze",
        lambda *args: PresentationPlan(summary="Synthetic assistance"),
    )
    result = create(
        client,
        batch(
            [recording],
            framing="auto",
            presentation_mode="auto",
            headline_position="auto",
            caption_position="auto",
        ),
    )
    assert result["status"] == "ready", result
    item = result["items"][0]
    assert item["headline_position"] == item["caption_position"] == "auto"
    assert item["export"]["auto_layout"]["headline_position"] == "centre"
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{item['id']}",
        json=edit(item, caption_position="lower"),
    )
    assert response.status_code == 202, response.text
    changed = finished(client, result["id"])
    assert changed["status"] == "ready"
    assert len(calls) == 2  # Different text constraints must not reuse a placement decision.
    current = changed["items"][0]
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{current['id']}",
        json=edit(
            current,
            caption_position="auto",
            overlays=[
                {
                    "text": "Keep this CTA",
                    "kind": "cta",
                    "start": 0.5,
                    "end": 2.0,
                    "position": "lower",
                    "style": "card",
                }
            ],
        ),
    )
    assert response.status_code == 202, response.text
    changed = finished(client, result["id"])
    assert changed["status"] == "ready", changed
    assert changed["items"][0]["export"]["auto_layout"]["mode"] == "pad"
    assert "text placement" in changed["items"][0]["export"]["auto_layout"]["summary"]


def test_auto_follow_success_snapshot_is_separate_and_reused(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []
    monkeypatch.setattr(
        manager.jobs.layout,
        "analyze",
        lambda source, recording, start, end, cancel: AutoLayoutPlan(
            start=start, end=end, mode="follow", summary="Synthetic follow"
        ),
    )

    def valid_follow(source, start, end, *args):
        calls.append(True)
        return FollowPlan(
            start=start,
            end=end,
            strategy="crop",
            width=0.3,
            height=1,
            points=[{"at": start, "x": 0.3, "y": 0}],
        )

    monkeypatch.setattr(manager.jobs.follow, "analyze", valid_follow)
    result = create(client, batch([recording], framing="auto"))
    assert result["status"] == "ready", result
    item = result["items"][0]
    assert item["export"]["auto_layout"]["mode"] == "follow" and item["export"]["follow_plan"]
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{item['id']}",
        json=edit(item, title="Another headline"),
    )
    assert response.status_code == 202, response.text
    result = finished(client, result["id"])
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["crop", "split"])
def test_actual_auto_crop_or_split_pixels_dimensions_and_audio(
    settings, stream_source, tmp_path, mode
):
    camera = SourceRegion(x=0.75, y=0, width=0.25, height=0.24)
    plan = AutoLayoutPlan(
        start=0,
        end=2,
        mode=mode,
        summary="Synthetic layout",
        crop_region=camera if mode == "crop" else None,
        split_layout=SplitLayout(webcam=camera, gameplay=SourceRegion(x=0, y=0, width=1, height=1))
        if mode == "split"
        else None,
    )
    output = tmp_path / "auto.mp4"
    info = Media(settings).render(
        stream_source,
        output,
        0,
        2,
        "auto",
        threading.Event(),
        tmp_path / "auto.log",
        auto_layout=plan,
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    assert info.duration == pytest.approx(2, abs=0.1)
    colour = pixel(rgb(settings, output, 0.5), 540, 100)
    assert colour[0] > 180 and colour[2] < 50, colour


def test_auto_cancel_during_analysis_saves_no_decision(context, monkeypatch):
    client, manager, recording, _, _ = context
    entered = threading.Event()

    def analyze(source, recording, start, end, cancel):
        entered.set()
        assert cancel.wait(3)
        raise RenderCancelled()

    monkeypatch.setattr(manager.jobs.layout, "analyze", analyze)
    response = client.post(
        f"/api/recordings/{recording.id}/clips", json={"start": 0, "end": 2, "mode": "auto"}
    )
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    assert entered.wait(3)
    assert client.post(f"/api/clips/{identifier}/cancel", json={}).status_code == 200
    result = await_export(client, identifier)
    assert result["status"] == "cancelled" and result["auto_layout"] is None
