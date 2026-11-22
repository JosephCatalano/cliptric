"""Export first-frame covers, caption placement snapshots and thumbnail recovery."""

import json
import subprocess
import threading
from uuid import uuid4

import pytest

from backend.captions import write_ass
from backend.headlines import HeadlineOptions, write_headline
from backend.media import Media, MediaError
from backend.models import CaptionOptions, ExportRequest
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_headlines import frame_pixels
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def test_thumbnail_comes_from_export_frame_zero_and_retries_without_rerender(context, monkeypatch):
    client, manager, recording, provider, _ = context
    calls = []
    original = manager.media.thumbnail

    def capture(source, output, timestamp, cancel):
        calls.append((source, timestamp))
        return original(source, output, timestamp, cancel)

    monkeypatch.setattr(manager.media, "thumbnail", capture)
    result = create(client, payload(recording))
    item = result["items"][0]
    assert item["thumbnail_export_id"] == item["export_id"]
    assert (manager.store.export_path(item["export_id"]), 0) in calls
    bundle_url = f"/api/projects/{result['id']}/thumbnail?item_id={item['id']}"
    response = client.get(bundle_url + "&download=true")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "image/jpeg"
    path = manager.thumbnail_path(manager.get(result["id"]), manager.get(result["id"]).items[0])
    path.unlink()
    count = len(manager.media.renders)
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert resumed["items"][0]["thumbnail"] and path.exists()
    assert len(manager.media.renders) == count and len(provider.calls) == 1


def test_thumbnail_failure_is_visible_and_resume_retries_export_frame(context, monkeypatch):
    client, manager, recording, _, _ = context
    original = manager.media.thumbnail

    def fail_export(source, *args):
        if source.parent.name == "exports":
            raise MediaError("Synthetic thumbnail failure")
        return original(source, *args)

    monkeypatch.setattr(manager.media, "thumbnail", fail_export)
    result = create(client, payload(recording))
    item = result["items"][0]
    assert item["export"]["status"] == "succeeded" and not item["thumbnail"]
    assert item["thumbnail_error"]
    count = len(manager.media.renders)
    monkeypatch.setattr(manager.media, "thumbnail", original)
    client.post(f"/api/projects/{result['id']}/resume", json={})
    item = finished(client, result["id"])["items"][0]
    assert item["thumbnail"] and not item["thumbnail_error"]
    assert len(manager.media.renders) == count


def test_upper_caption_profile_and_legacy_snapshot_are_distinct(context, tmp_path):
    _, manager, recording, _, _ = context
    transcript = manager.transcriptions.latest(recording.id)
    options = CaptionOptions(
        transcription_id=transcript.id, revision=transcript.revision, position="upper"
    )
    clip = manager.store.create_clip(
        str(uuid4()), recording.id, ExportRequest(start=0.0, end=3.0, captions=options), []
    )
    assert manager.store.clip(clip.id).captions.presentation_version == 2
    with manager.store.connection() as db:
        old = options.model_dump(mode="json")
        del old["presentation_version"]
        db.execute("UPDATE clip_captions SET options=? WHERE clip_id=?", (json.dumps(old), clip.id))
    assert manager.store.clip(clip.id).captions.presentation_version == 1
    assert manager.store.clips(recording.id)[0].captions.presentation_version == 1
    words = [{"start": 0.0, "end": 1.0, "text": "Useful words"}]
    for version, margin in ((1, 260), (2, 350)):
        path = tmp_path / f"caption-{version}.ass"
        write_ass(words, 0, 2, path, position="upper", presentation_version=version)
        assert f",8,100,100,{margin},1" in path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize(
    "table,field", [("clip_headlines", "appearance"), ("clip_captions", "presentation_version")]
)
def test_explicit_resume_updates_legacy_style_without_repeating_analysis(context, table, field):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    original = result["items"][0]["export_id"]
    with manager.store.connection() as db:
        options = json.loads(
            db.execute(f"SELECT options FROM {table} WHERE clip_id=?", (original,)).fetchone()[0]
        )
        del options[field]
        db.execute(f"UPDATE {table} SET options=? WHERE clip_id=?", (json.dumps(options), original))
    renders = len(manager.media.renders)
    client.post(f"/api/projects/{result['id']}/resume", json={})
    updated = finished(client, result["id"])
    assert updated["status"] == "ready"
    assert updated["items"][0]["export_id"] != original
    assert len(manager.media.renders) == renders + 1 and len(provider.calls) == 1
    assert manager.store.export_path(original).is_file()
    assert manager.store.clip(original).status == "succeeded"
    assert manager.store.export_path(original).is_file()
    assert len(manager.media.renders) == renders + 1 and len(provider.calls) == 1
    old = manager.store.clip(original)
    assert (
        (old.headline.appearance == "classic")
        if field == "appearance"
        else (old.captions.presentation_version == 1)
    )


def test_cover_visible_on_first_frame_with_lower_upper_captions_and_jpeg(caption_media, tmp_path):
    settings, source = caption_media
    headline = tmp_path / "cover.ass"
    captions = tmp_path / "upper.ass"
    write_headline(
        headline, HeadlineOptions(text="An opening worth watching", seconds=1), 3, "upper"
    )
    write_ass(
        [{"start": 0.3, "end": 1.0, "text": "Useful words"}],
        0,
        3,
        captions,
        position="upper",
        preset="impact",
    )
    output = tmp_path / "cover.mp4"
    media = Media(settings)
    info = media.render(
        source,
        output,
        0,
        3,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        headline_path=headline,
        caption_path=captions,
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    first = frame_pixels(settings, output, 0)
    assert sum(value > 220 for value in first) > 30000
    assert min(i // 1080 for i, value in enumerate(first) if value > 220) > 650
    speaking = frame_pixels(settings, output, 0.5)
    assert sum(speaking[y * 1080 + x] > 220 for y in range(340, 530) for x in range(100, 980)) > 100
    assert not any(value > 100 for value in frame_pixels(settings, output, 1.5))
    preview = tmp_path / "cover.jpg"
    media.thumbnail(output, preview, 0, threading.Event())
    assert preview.read_bytes().startswith(b"\xff\xd8")
    result = subprocess.run(
        [
            settings.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(preview),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    dimensions = json.loads(result.stdout)["streams"][0]
    assert (dimensions["width"], dimensions["height"]) == (360, 640)
