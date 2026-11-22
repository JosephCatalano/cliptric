"""Real overlay exports plus isolated hook and project persistence checks."""

import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.headlines import HeadlineOptions
from backend.hooks import suggest_hooks
from backend.media import Media
from backend.models import ExportRequest
from backend.overlays import TextOverlay, layout, write_presentation
from backend.project_store import ProjectStore
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import frame_pixels
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_projects import settings as settings
from backend.tests.test_projects import tools as tools


@pytest.mark.parametrize(
    "changes",
    [
        {"text": " "},
        {"text": "x" * 121},
        {"text": "bad\u202etext"},
        {"start": -1},
        {"start": True},
        {"end": float("nan")},
        {"end": float("inf")},
        {"start": 1, "end": 1},
        {"end": 21601},
        {"style": "arbitrary"},
        {"position": "free"},
    ],
)
def test_invalid_overlays(changes):
    with pytest.raises(ValidationError):
        TextOverlay.model_validate({"text": "A title", "end": 2, **changes})


def test_collision_and_trim_bounds():
    assert TextOverlay(text="Brief", start=0.2, end=0.3).end == 0.3
    overlay = TextOverlay(text="Read more", start=0, end=2)
    assert layout([overlay], 3, HeadlineOptions(text="Opening"), "lower")[0][1] == "centre"
    assert [
        p for _, p in layout([overlay, overlay.model_copy(update={"position": "upper"})], 3)
    ] == ["centre", "upper"]
    with pytest.raises(ValueError, match="collide"):
        layout([overlay.model_copy(update={"position": "lower"})], 3, None, "lower")
    assert [
        p for _, p in layout([overlay, overlay.model_copy(update={"start": 2, "end": 3})], 3)
    ] == ["upper", "upper"]
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=1, overlays=[overlay])
    with pytest.raises(ValidationError):
        ExportRequest(start=0, end=3, overlays=[overlay] * 7)


def test_escaping_and_timing(tmp_path):
    path = tmp_path / "text.ass"
    write_presentation(path, [TextOverlay(text=r"{\pos(0,0)} safe", start=1, end=2)], 3)
    content = path.read_text(encoding="utf-8-sig")
    assert "0:00:01.00,0:00:02.00" in content
    assert r"{\pos(0,0)}" not in content
    assert "Style: OverlayBox" in content and ",0,3,0,0,5," in content
    assert content.count("Dialogue:") == 1


@pytest.mark.parametrize("style", ["card", "outline", "violet"])
def test_real_timed_overlay_export(tmp_path, caption_media, style):
    settings, source = caption_media
    path = tmp_path / "overlay.ass"
    write_presentation(
        path,
        [TextOverlay(text="Read the full story", start=1, end=2, position="centre", style=style)],
        3,
    )
    output = tmp_path / "overlay.mp4"
    info = Media(settings).render(
        source, output, 0, 3, "pad", threading.Event(), tmp_path / "render.log", headline_path=path
    )
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio and info.duration == pytest.approx(3, abs=0.1)
    assert max(frame_pixels(settings, output, 0.5)) < 100
    pixels = frame_pixels(settings, output, 1.5)
    assert sum(value > 100 for value in pixels) > 1000
    assert max(frame_pixels(settings, output, 2.5)) < 100


def test_hook_uses_complete_segment_and_exact_saved_evidence():
    words = [
        dict(
            id=i,
            start=float(i * 2),
            end=float(i * 2 + 1),
            text=text,
            probability=0.9,
            original_start=float(i * 2),
            original_end=float(i * 2 + 1),
        )
        for i, text in enumerate(["Earlier context.", "The insight", "is useful.", "The payoff."])
    ]
    candidate = SimpleNamespace(evidence="is useful.")
    hooks = suggest_hooks(words, candidate, 0, 8)
    assert len(hooks) == 1 and hooks[0].start == 2
    assert hooks[0].opening_text == "is useful."
    assert "Earlier context." in hooks[0].context
    assert suggest_hooks(words, SimpleNamespace(evidence="Invented dialogue"), 0, 8) == []
    assert suggest_hooks(words, candidate, 5, 8) == []
    assert suggest_hooks(words, candidate, 0, 4.5) == []


def test_project_overlay_save_reopen_immutable_render_and_hook_endpoint(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    item, other = result["items"]
    url = f"/api/projects/{result['id']}/clips/{item['id']}"
    calls, renders = len(provider.calls), len(manager.media.renders)
    response = client.get(url + "/hooks")
    assert response.status_code == 200, response.text
    assert response.json()[0]["evidence"] == "Evidence 2."
    assert client.get(f"/api/projects/{result['id']}/clips/{uuid4()}/hooks").status_code == 404
    edit = {
        key: item[key]
        for key in ("title", "start", "end", "revision", "caption_preset", "caption_position")
    }
    edit.update(
        opening_text="A separate opening",
        overlays=[
            dict(text="Full video", kind="cta", start=5, end=8, style="outline", position="auto")
        ],
    )
    response = client.patch(url, json=edit)
    assert response.status_code == 202, response.text
    changed = finished(client, result["id"])
    assert changed["status"] == "ready", changed
    updated = changed["items"][0]
    assert updated["export"]["overlays"] == edit["overlays"]
    assert updated["export"]["headline"]["text"] == "A separate opening"
    assert manager.store.clip(item["export_id"]).overlays == []
    assert changed["items"][1]["export_id"] == other["export_id"]
    assert manager.store.clips(recording.id)[0].overlays[0].text == "Full video"
    reopened = ProjectStore(manager.store).get(result["id"])
    assert reopened.items[0].overlays[0].text == "Full video"
    assert reopened.items[0].opening_text == "A separate opening"
    assert len(provider.calls) == calls and len(manager.media.renders) == renders + 1
    assert client.patch(url, json=edit).status_code == 409
    edit["revision"] = updated["revision"]
    edit["end"] = edit["start"] + 6
    assert client.patch(url, json=edit).status_code == 422
    client.post(f"/api/projects/{result['id']}/resume", json={})
    finished(client, result["id"])
    assert len(manager.media.renders) == renders + 1
    from backend.transcription_models import WordsPatch

    manager.transcriptions.patch_words(
        result["transcription_id"],
        WordsPatch(
            revision=result["transcript_revision"], updates=[{"id": 2, "text": "Corrected source."}]
        ),
    )
    assert client.get(url + "/hooks").status_code == 409
