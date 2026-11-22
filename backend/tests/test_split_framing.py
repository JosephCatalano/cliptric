"""Actual composed media, validated regions, and isolated project orchestration."""

import subprocess
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.captions import write_ass
from backend.framing import SourceRegion, SplitLayout
from backend.media import Media, MediaError, RenderCancelled
from backend.models import ExportRequest, Recording
from backend.storage import Store
from backend.tests.test_pipeline import await_export, import_video
from backend.tests.test_pipeline import client as client
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def split_options(**changes):
    return SplitLayout.model_validate(
        {
            "webcam": {"x": 0.75, "y": 0, "width": 0.25, "height": 0.25},
            "gameplay": {"x": 0, "y": 0.25, "width": 1, "height": 0.75},
            "gameplay_fit": "fit",
            **changes,
        }
    )


@pytest.fixture(scope="module")
def stream_source(tmp_path_factory, tools):
    source = tmp_path_factory.mktemp("split-media") / "stream.mp4"
    subprocess.run(
        [
            tools.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=320x180:rate=12:duration=2,"
            "drawbox=x=0:y=45:w=320:h=135:color=yellow:t=fill:enable='gte(t,1)',"
            "drawbox=x=240:y=0:w=80:h=44:color=red:t=fill:enable='lt(t,1)',"
            "drawbox=x=240:y=0:w=80:h=44:color=lime:t=fill:enable='gte(t,1)'",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return source


def rgb(settings, path, seconds):
    return subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


def pixel(frame, x, y):
    offset = (y * 1080 + x) * 3
    return tuple(frame[offset : offset + 3])


@pytest.mark.parametrize(
    "changes",
    [
        {"x": -0.1},
        {"y": 1},
        {"width": 0},
        {"width": 1.1},
        {"height": float("nan")},
        {"x": True},
        {"y": "0"},
        {"x": 0.8, "width": 0.3},
        {"y": 0.9, "height": 0.2},
    ],
)
def test_invalid_source_regions(changes):
    with pytest.raises(ValidationError):
        SourceRegion.model_validate({"x": 0, "y": 0, "width": 0.5, "height": 0.5, **changes})


def test_framing_contract_requires_regions_only_for_split():
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, mode="split")
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, mode="pad", split_layout=split_options())
    with pytest.raises(ValueError, match="two source pixels"):
        SourceRegion(x=0, y=0, width=0.01, height=0.01).pixels(32, 18)


@pytest.mark.parametrize("fit", ["fit", "fill"])
def test_actual_split_output_panel_geometry_synchronization_and_audio(
    settings, stream_source, tmp_path, fit
):
    output = tmp_path / "composed.mp4"
    info = Media(settings).render(
        stream_source,
        output,
        0,
        2,
        "split",
        threading.Event(),
        tmp_path / "render.log",
        split_layout=split_options(gameplay_fit=fit),
    )
    assert (info.width, info.height, info.has_audio, info.sample_aspect_ratio) == (
        1080,
        1920,
        True,
        "1:1",
    )
    assert info.duration == pytest.approx(2, abs=0.1)
    before, after = rgb(settings, output, 0.5), rgb(settings, output, 1.5)
    # Camera ends exactly at row 639; gameplay starts at row 640.
    for y in (20, 320, 639):
        red = pixel(before, 540, y)
        green = pixel(after, 540, y)
        assert red[0] > 200 and red[1] < 40 and red[2] < 40
        assert green[1] > 200 and green[0] < 40 and green[2] < 40
    for y in (800, 1280, 1850):
        blue, yellow = pixel(before, 540, y), pixel(after, 540, y)
        assert blue[2] > 200 and blue[0] < 40
        assert yellow[0] > 200 and yellow[1] > 200 and yellow[2] < 40
    subprocess.run(
        [settings.ffmpeg, "-v", "error", "-i", str(output), "-f", "null", "-"],
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("transform", ["rotation", "sar", "silent"])
def test_split_handles_display_geometry_and_silent_media(
    settings, stream_source, tmp_path, transform
):
    source, output = tmp_path / "source.mp4", tmp_path / "export.mp4"
    if transform == "rotation":
        args = ["-display_rotation", "90", "-i", str(stream_source), "-c", "copy"]
    elif transform == "sar":
        args = ["-i", str(stream_source), "-vf", "setsar=2", "-c:v", "libx264", "-c:a", "copy"]
    else:
        args = ["-i", str(stream_source), "-an", "-c:v", "copy"]
    subprocess.run(
        [settings.ffmpeg, "-v", "error", *args, str(source)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    media = Media(settings)
    # Full-frame regions exercise normalization independently of where the camera rotated.
    full = dict(x=0, y=0, width=1, height=1)
    info = media.render(
        source,
        output,
        0,
        1,
        "split",
        threading.Event(),
        tmp_path / "render.log",
        split_layout=split_options(webcam=full, gameplay=full),
    )
    assert (info.width, info.height, info.sample_aspect_ratio) == (1080, 1920, "1:1")
    assert info.has_audio == (transform != "silent")


def test_split_caption_timing_after_composition(settings, stream_source, tmp_path):
    captions = tmp_path / "caption.ass"
    write_ass([dict(start=0.6, end=1.1, text="SYNC")], 0.25, 1.75, captions)
    output = tmp_path / "captioned.mp4"
    Media(settings).render(
        stream_source,
        output,
        0.25,
        1.75,
        "split",
        threading.Event(),
        tmp_path / "render.log",
        caption_path=captions,
        split_layout=split_options(),
    )

    def white_count(time):
        frame = rgb(settings, output, time)
        return sum(
            all(value > 220 for value in frame[i : i + 3])
            for i in range(1200 * 1080 * 3, 1650 * 1080 * 3, 3)
        )

    assert white_count(0.15) == 0
    assert white_count(0.6) > 200
    assert white_count(1.25) == 0


def test_split_draft_and_immutable_export_survive_reopen(client, settings, stream_source):
    recording = import_video(client, stream_source)
    url = f"/api/recordings/{recording['id']}"
    options = split_options().model_dump()
    saved = client.put(
        url + "/draft", json=dict(start=0, end=1, mode="split", split_layout=options, revision=0)
    )
    assert saved.status_code == 200, saved.text
    assert client.get(url + "/draft").json()["split_layout"] == options
    response = client.post(
        url + "/clips", json=dict(start=0, end=1, mode="split", split_layout=options)
    )
    assert response.status_code == 202, response.text
    clip = await_export(client, response.json()["id"])
    assert clip["status"] == "succeeded", clip
    reopened = Store(settings.data_dir)
    assert reopened.clip(clip["id"]).split_layout.model_dump() == options
    assert reopened.clips(recording["id"])[0].split_layout.model_dump() == options
    options["webcam"]["x"] = 0
    assert reopened.clip(clip["id"]).split_layout.webcam.x == 0.75
    assert client.get(f"/api/clips/{clip['id']}/media?download=true").status_code == 200


def test_project_split_creation_and_per_clip_override_reuses_analysis(context):
    client, manager, recording, provider, _ = context
    options = split_options().model_dump()
    project = create(client, payload(recording, framing="split", split_layout=options))
    assert project["status"] == "ready", project
    first, other = project["items"]
    assert first["export"]["mode"] == "split" and first["export"]["split_layout"] == options
    url = f"/api/projects/{project['id']}/clips/{first['id']}"
    edit = {
        key: first[key]
        for key in ("title", "start", "end", "revision", "caption_preset", "caption_position")
    }
    replacement = split_options(webcam=dict(x=0, y=0, width=0.25, height=0.25)).model_dump()
    calls, renders = len(provider.calls), len(manager.media.renders)
    response = client.patch(url, json={**edit, "framing": "split", "split_layout": replacement})
    assert response.status_code == 202, response.text
    changed = finished(client, project["id"])
    assert changed["status"] == "ready", changed
    updated = changed["items"][0]
    assert updated["export"]["split_layout"] == replacement
    assert manager.records.get(project["id"]).items[0].split_layout.webcam.x == 0
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clip(first["export_id"]).split_layout.webcam.x == 0.75
    assert len(provider.calls) == calls and len(manager.media.renders) == renders + 1
    assert (
        client.patch(
            url, json={**edit, "framing": "split", "split_layout": replacement}
        ).status_code
        == 409
    )
    client.post(f"/api/projects/{project['id']}/resume", json={})
    finished(client, project["id"])
    assert len(manager.media.renders) == renders + 1


def test_split_validates_direct_media_calls_and_cancellation(settings, stream_source, tmp_path):
    media = Media(settings)
    with pytest.raises(MediaError, match="requires"):
        media.render(
            stream_source,
            tmp_path / "invalid.mp4",
            0,
            1,
            "split",
            threading.Event(),
            tmp_path / "invalid.log",
        )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RenderCancelled):
        media.render(
            stream_source,
            tmp_path / "cancel.mp4",
            0,
            1,
            "split",
            cancelled,
            tmp_path / "cancel.log",
            split_layout=split_options(),
        )
    assert not (tmp_path / "cancel.mp4").exists()


def test_migrate_version_four_preserves_legacy_export(settings):
    store = Store(settings.data_dir)
    source = Recording(
        id=str(uuid4()),
        name="Original.mp4",
        duration=2,
        width=320,
        height=180,
        has_audio=False,
        size_bytes=1,
        created_at="2026-11-18T00:00:00Z",
    )
    store.save_recording(source, ".mp4")
    clip = store.create_clip(str(uuid4()), source.id, ExportRequest(start=0, end=1))
    with store.connection() as db:
        db.execute("DROP TABLE clip_framing")
        db.execute("PRAGMA user_version=4")
    reopened = Store(settings.data_dir)
    assert reopened.clip(clip.id) == clip
    assert reopened.recording(source.id) == source
    with reopened.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6


def test_thin_region_is_cropped_before_upscaling(settings, stream_source, tmp_path):
    info = Media(settings).render(
        stream_source,
        tmp_path / "thin.mp4",
        0,
        1,
        "split",
        threading.Event(),
        tmp_path / "thin.log",
        split_layout=split_options(webcam=dict(x=0, y=0, width=0.01, height=1)),
    )
    assert (info.width, info.height) == (1080, 1920)
