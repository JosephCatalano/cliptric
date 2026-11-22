"""Setup downloads and real exports for refreshed caption presentation."""

import threading
import time

import pytest
from pydantic import ValidationError

from backend.captions import write_ass
from backend.headlines import HeadlineOptions
from backend.media import Media
from backend.models import CaptionOptions, ExportRequest
from backend.overlays import TextOverlay, resolve_caption_position
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import frame_pixels
from backend.tests.test_deferred_projects import deferred as deferred
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import finished


@pytest.mark.parametrize(
    "preset,expected", [("impact", r"\fscx85"), ("studio", r"\fad("), ("focus", "&H00EEDD66")]
)
def test_new_caption_styles_are_actually_rendered(tmp_path, caption_media, preset, expected):
    settings, source = caption_media
    path = tmp_path / "captions.ass"
    words = [dict(start=0.5, end=1.0, text="A real"), dict(start=1.0, end=1.5, text="moment.")]
    write_ass(words, 0, 2, path, preset=preset, position="auto")
    text = path.read_text(encoding="utf-8-sig")
    assert expected in text
    if preset == "studio":
        assert "Dialogue: 0" in text and "&H304C2234" in text
    output = tmp_path / "new-style.mp4"
    info = Media(settings).render(
        source, output, 0, 2, "pad", threading.Event(), tmp_path / "render.log", caption_path=path
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    assert info.duration == pytest.approx(2, abs=0.1)
    assert max(frame_pixels(settings, output, 0.2)) < 100
    assert sum(value > 100 for value in frame_pixels(settings, output, 0.8)) > 1000
    assert max(frame_pixels(settings, output, 1.8)) < 100


def test_auto_placement_avoids_overlays_and_split_webcam():
    headline = HeadlineOptions(text="Opening")
    lower = TextOverlay(text="CTA", start=0, end=2, position="lower")
    centre = TextOverlay(text="Middle", start=0, end=2, position="centre")
    assert resolve_caption_position("auto", [], 3, headline, True) == "lower"
    assert resolve_caption_position("auto", [lower], 3, headline, True) == "centre"
    with pytest.raises(ValueError, match="No caption region"):
        resolve_caption_position("auto", [lower, centre], 3, None, True)
    assert resolve_caption_position("auto", [lower, centre], 3) == "upper"
    assert resolve_caption_position("upper", [lower], 3, headline, True) == "upper"


def test_split_auto_rejects_collisions_before_render():
    from backend.tests.test_split_framing import split_options

    with pytest.raises(ValidationError, match="No caption region"):
        ExportRequest(
            start=0,
            end=3,
            mode="split",
            split_layout=split_options(),
            captions=CaptionOptions(
                transcription_id="00000000-0000-0000-0000-000000000001", revision=1, position="auto"
            ),
            overlays=[
                TextOverlay(text="Lower", end=2, position="lower"),
                TextOverlay(text="Centre", end=2, position="centre"),
            ],
        )


def test_setup_download_is_shared_with_create_without_early_ai(deferred):
    client, manager, source, request, events, control, entered, release, provider, _ = deferred
    control["hold"] = True
    response = client.post("/api/youtube-imports", json={"url": source["url"]})
    assert response.status_code == 202, response.text
    download_id = response.json()["id"]
    assert entered.wait(5)
    assert events == ["preview", "download"] and not provider.calls
    assert client.get("/api/projects").json() == []
    # Strict-mode remount/retry reuses the in-progress source transfer.
    assert (
        client.post("/api/youtube-imports", json={"url": source["url"]}).json()["id"] == download_id
    )
    job = manager.youtube.get(download_id)
    job.downloaded_bytes, job.total_bytes = 50, 100
    manager.youtube.save(job)
    progress = client.get(f"/api/youtube-imports/{download_id}").json()
    assert progress["downloaded_bytes"] / progress["total_bytes"] == 0.5
    assert client.post("/api/projects", json=request).status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        project = client.get(f"/api/projects/{request['id']}").json()
        if project["download_id"] == download_id:
            break
        time.sleep(0.02)
    assert project["download_id"] == download_id
    release.set()
    result = finished(client, request["id"])
    assert result["status"] == "ready", result
    draft = manager.store.draft(manager.store.recording(result["recording_id"]))
    assert draft.caption_preset == "impact" and draft.caption_position == "auto"
    assert events.count("download") == 1 and events.count("transcribe") == 1
    assert len(provider.calls) == 1
    assert (
        client.post("/api/youtube-imports", json={"url": source["url"]}).json()["id"] == download_id
    )
