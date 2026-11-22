"""Single-face logic, immutable plans, and actual moving-crop encoding."""

import threading

import pytest
from pydantic import ValidationError

from backend.auto_follow import AutoFollow, tracking_plan
from backend.editing import QualityOptions, Removal
from backend.follow_models import FollowPlan, follow_fit
from backend.follow_render import follow_commands
from backend.media import Media, MediaError, RenderCancelled
from backend.models import ExportRequest
from backend.presentation import PresentationPlan
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


def observations(xs):
    return [([] if x is None else [(x, 0.2, 0.12, 0.22)], []) for x in xs]


def test_track_has_headroom_smoothing_deadzone_and_brief_gap_hold():
    plan = tracking_plan(observations([0.3, 0.3, 0.32, None, 0.34, 0.36]), 1920, 1080, 5, 8)
    assert plan.strategy == "crop", plan.warnings
    assert plan.points[0].at == 5 and plan.points[-1].at == 8
    assert plan.points[0].x == plan.points[1].x
    assert plan.points[3].x == plan.points[2].x
    assert 0 < plan.points[4].x - plan.points[3].x < 0.04
    for point in plan.points:
        assert 0 <= point.x <= 1 - plan.width and 0 <= point.y <= 1 - plan.height


@pytest.mark.parametrize(
    "frames",
    [
        observations([None] * 6),
        observations([0.3, None, None, None, None]),
        [([(0.2, 0.2, 0.1, 0.2), (0.6, 0.2, 0.1, 0.2)], [])] * 4,
        observations([0.1, None, 0.7]),
    ],
)
def test_ambiguous_targets_preserve_full_picture(frames):
    plan = tracking_plan(frames, 1920, 1080, 0, 3)
    assert plan.strategy == "fit" and plan.width == plan.height == 1
    assert plan.warnings


def test_existing_text_is_preserved_and_dense_plan_is_separate_from_request():
    frames = observations([0.3] * 5)
    frames[2][1].append((0.0, 0.8, 1.0, 0.9))
    assert tracking_plan(frames, 1920, 1080, 0, 3).strategy == "fit"
    with pytest.raises(ValidationError):
        ExportRequest(
            start=0, end=2, mode="follow", crop_region={"x": 0, "y": 0, "width": 1, "height": 1}
        )
    assert ExportRequest(start=0, end=2, mode="follow").mode == "follow"


@pytest.mark.parametrize("count", [2, 1201])
def test_actual_commands_move_crop_and_preserve_audio_through_removals(
    settings, stream_source, tmp_path, count
):
    plan = FollowPlan(
        start=0,
        end=2,
        strategy="crop",
        width=0.3125,
        height=1.0,
        points=[
            {"at": 2 * i / (count - 1), "x": 0.6875 * i / (count - 1), "y": 0.0}
            for i in range(count)
        ],
    )
    quality = QualityOptions(cover_at=1.75, removals=[Removal(start=0.5, end=1.0, reason="manual")])
    commands = follow_commands(plan, [(0, 0.5), (1, 2)], 320, 180)
    assert "0.50000000" in commands and "crop@follow x" in commands
    output = tmp_path / "follow.mp4"
    media = Media(settings)
    cover = tmp_path / "cover.jpg"
    media.thumbnail(stream_source, cover, 1.75, threading.Event())
    info = media.render(
        stream_source,
        output,
        0,
        2,
        "follow",
        threading.Event(),
        tmp_path / "follow.log",
        follow_plan=plan,
        quality=quality,
        cover_path=cover,
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    assert info.duration == pytest.approx(1.5, abs=0.1)
    opening = pixel(rgb(settings, output, 0.1), 540, 100)
    assert opening[1] > 180 and opening[2] < 50, opening
    before = pixel(rgb(settings, output, 0.35), 540, 100)
    after = pixel(rgb(settings, output, 1.35), 540, 100)
    assert before[2] > 180 and before[0] < 50, before
    assert after[1] > 180 and after[0] < 50 and after[2] < 50, after
    assert not list(tmp_path.glob("clipping-captions-*"))


def test_analyzer_cancellation_and_long_clip_budget(settings, stream_source, tmp_path):
    follow = AutoFollow(Media(settings))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RenderCancelled):
        follow.analyze(stream_source, 0, 2, cancel, tmp_path)
    cancel.clear()
    assert follow.analyze(stream_source, 0, 601, cancel, tmp_path).strategy == "fit"
    actual = follow.analyze(stream_source, 0, 2, cancel, tmp_path)
    assert actual.strategy == "fit" and actual.warnings
    assert not list(tmp_path.glob("*.follow.partial.*"))


def test_project_inherits_follow_caches_plan_and_manual_override(context, monkeypatch):
    client, manager, recording, provider, configured = context
    calls = []

    def analyze(source, start, end, *args):
        calls.append((start, end))
        return follow_fit(start, end, "Synthetic no-face fallback")

    monkeypatch.setattr(manager.jobs.follow, "analyze", analyze)
    value = create(client, payload(recording, framing="follow"))
    assert value["status"] == "ready", value
    first = value["items"][0]
    original = first["export_id"]
    draft_route = f"/api/recordings/{recording.id}/draft"
    draft = client.put(
        draft_route,
        json={"start": first["start"], "end": first["end"], "mode": "follow", "revision": 0},
    )
    assert draft.status_code == 200, draft.text
    assert client.get(draft_route).json()["mode"] == "follow"
    assert first["export"]["follow_plan"]["warnings"] == ["Synthetic no-face fallback"]
    assert len(calls) == 2
    route = f"/api/projects/{value['id']}/clips/{first['id']}"
    response = client.patch(route, json=edit(first, title="Changed headline"))
    assert response.status_code == 202, response.text
    changed = finished(client, value["id"])
    assert len(calls) == 2 and len(provider.calls) == 1
    reopened = Store(configured.data_dir)
    assert reopened.clip(original).follow_plan.strategy == "fit"
    assert reopened.clips(recording.id)[0].follow_plan is not None
    current = changed["items"][0]
    response = client.patch(route, json=edit(current, start=current["start"] + 0.5))
    assert response.status_code == 202, response.text
    changed = finished(client, value["id"])
    assert len(calls) == 3
    current = changed["items"][0]
    response = client.patch(
        route,
        json=edit(
            current, framing="crop", crop_region={"x": 0.0, "y": 0.0, "width": 0.3, "height": 1.0}
        ),
    )
    assert response.status_code == 202, response.text
    manual = finished(client, value["id"])
    assert manual["items"][0]["export"]["follow_plan"] is None
    assert len(calls) == 3


def test_batch_follow_is_per_source(context, monkeypatch):
    client, manager, recording, _, _ = context
    monkeypatch.setattr(
        manager.jobs.follow,
        "analyze",
        lambda source, start, end, *args: follow_fit(start, end, "Synthetic fallback"),
    )
    monkeypatch.setattr(
        manager.presentation,
        "analyze",
        lambda *args: PresentationPlan(summary="Synthetic proposal"),
    )
    value = create(client, batch([recording], framing="follow", presentation_mode="auto"))
    assert value["status"] == "ready", value
    assert value["items"][0]["export"]["mode"] == "follow"


@pytest.mark.parametrize("fault", ["cancel", "error"])
def test_follow_cancellation_and_detector_failure_are_visible(context, monkeypatch, fault):
    client, manager, recording, _, _ = context
    entered = threading.Event()

    def analyze(source, start, end, cancel, *args):
        entered.set()
        if fault == "error":
            raise MediaError("Synthetic detector failure")
        assert cancel.wait(3), "Test did not cancel the active export"
        raise RenderCancelled()

    monkeypatch.setattr(manager.jobs.follow, "analyze", analyze)
    response = client.post(
        f"/api/recordings/{recording.id}/clips", json={"start": 10, "end": 19, "mode": "follow"}
    )
    assert response.status_code == 202, response.text
    clip_id = response.json()["id"]
    assert entered.wait(3)
    if fault == "cancel":
        response = client.post(f"/api/clips/{clip_id}/cancel", json={})
        assert response.status_code == 200, response.text
    clip = await_export(client, clip_id)
    assert clip["status"] == ("cancelled" if fault == "cancel" else "failed"), clip
    assert clip["follow_plan"] is None
    if fault == "error":
        assert clip["error"] == "Synthetic detector failure"


def test_non_grid_duration_and_nonzero_start_decode(settings, stream_source, tmp_path):
    result = AutoFollow(Media(settings)).analyze(
        stream_source, 0.2, 1.3, threading.Event(), tmp_path
    )
    assert (result.start, result.end, result.strategy) == (0.2, 1.3, "fit")


def test_follow_scratch_recovery_preserves_other_files(context):
    _, manager, _, _, _ = context
    root = manager.store.root / "exports"
    scratch = [root / "interrupted.follow.partial.gray", root / "interrupted.follow.partial.log"]
    untouched = root / "keep.mp4"
    for path in [*scratch, untouched]:
        path.write_bytes(b"synthetic artifact")
    manager.store.recover()
    assert not any(path.exists() for path in scratch)
    assert untouched.read_bytes() == b"synthetic artifact"
