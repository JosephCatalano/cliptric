"""Multi-source review and local presentation checks with explicit test adapters."""

import asyncio
import io
import json
import subprocess
import threading
import zipfile
from dataclasses import replace

import cv2
import httpx2
import numpy as np
import pytest
from pydantic import ValidationError

from backend.batch_review import MultiBatchReview, MultiReviewProposal
from backend.captions import write_ass
from backend.editing import FramingPoint, QualityOptions, SmartFraming
from backend.headlines import HeadlineOptions, write_headline
from backend.media import Media
from backend.models import ExportRequest
from backend.presentation import AutoPresentation, PresentationPlan, plan_frames, text_regions
from backend.tests.test_batch_review import proposal
from backend.tests.test_batches import batch, create, edit
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import finished


def choices(count=2):
    candidates = []
    for first, last in ((0, 4), (8, 12), (13, 15))[:count]:
        candidate = proposal("trim").candidate.model_copy(deep=True)
        candidate.first_segment, candidate.last_segment = first, last
        candidate.title = f"A distinct moment {first}"
        candidate.evidence = f"Evidence {first}."
        candidate.quality.opening_evidence = f"Evidence {first}."
        candidate.quality.ending_evidence = f"Evidence {last}."
        candidates.append(candidate)
    return MultiReviewProposal(
        decision="clips", candidates=candidates, reason="Distinct complete moments."
    )


def request(recording, **settings):
    return {
        **batch(
            [recording], batch_mode="highlights", clips_per_source=3, quality_mode=True, **settings
        ),
        "remote_consent": True,
    }


def test_multiple_clips_share_transcript_review_and_survive_retry_deletion(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    async def review(*args, **kwargs):
        calls.append(kwargs)
        return choices(), 100, 30

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    result = create(client, request(recording))
    assert result["status"] == "ready", result
    assert len(result["items"]) == 2 and len(calls) == 1
    items = result["items"]
    assert len({i["transcription_id"] for i in items}) == 1
    assert sum(i["analysis_input_tokens"] for i in items) == 100
    assert all(i["analysis_complete"] and i["export"]["status"] == "succeeded" for i in items)
    assert len({i["source_item_id"] for i in items}) == 1
    exports = [i["export_id"] for i in items]
    client.post(f"/api/projects/{result['id']}/resume", json={})
    resumed = finished(client, result["id"])
    assert [i["export_id"] for i in resumed["items"]] == exports and len(calls) == 1
    response = client.get(f"/api/projects/{result['id']}/bundle")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(json.loads(archive.read("manifest.json"))["included"]) == 2
    assert (
        client.delete(f"/api/projects/{result['id']}/clips/{items[0]['id']}?revision=0").status_code
        == 200
    )
    client.post(f"/api/projects/{result['id']}/resume", json={})
    assert len(finished(client, result["id"])["items"]) == 1 and len(calls) == 1


@pytest.mark.parametrize("fault", ["overlap", "evidence", "limit"])
def test_multi_review_rejects_all_invalid_results_before_rendering(context, monkeypatch, fault):
    client, manager, recording, _, _ = context
    value = choices()
    if fault == "overlap":
        value.candidates[1] = value.candidates[0].model_copy(deep=True)
    elif fault == "evidence":
        value.candidates[1].evidence = "Speech never present in this source"

    async def review(*args, **kwargs):
        return value, 100, 30

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    payload = request(recording)
    if fault == "limit":
        payload["settings"]["clips_per_source"] = 1
    result = create(client, payload)
    assert result["status"] == "partial" and len(result["items"]) == 1
    assert result["items"][0]["analysis_error"] and not manager.media.renders


def test_multi_keep_whole_and_explicit_billing_retry(context, monkeypatch):
    from backend.highlight_models import HighlightError

    client, manager, recording, _, _ = context
    calls = []

    async def review(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise HighlightError("Synthetic uncertain request")
        return (
            MultiReviewProposal(
                decision="keep_full", candidates=[proposal().candidate], reason="Complete story."
            ),
            10,
            5,
        )

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    result = create(client, request(recording))
    client.post(f"/api/projects/{result['id']}/resume", json={})
    assert finished(client, result["id"])["status"] == "partial" and len(calls) == 1
    client.post(f"/api/projects/{result['id']}/resume", json={"retry_analysis": True})
    value = finished(client, result["id"])
    assert value["status"] == "ready" and len(value["items"]) == 1
    assert (value["items"][0]["start"], value["items"][0]["end"]) == (0, recording.duration)


def test_multi_transport_sends_bound_and_strict_schema(settings):
    def respond(http_request):
        body = json.loads(http_request.content)
        assert json.loads(body["input"])["max_clips"] == 3
        assert body["text"]["format"]["schema"]["properties"]["candidates"]["maxItems"] == 5
        assert "max_clips" in body["instructions"] and body["store"] is False
        return httpx2.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": choices().model_dump_json()}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    reviewer = MultiBatchReview(
        replace(settings, openai_api_key="synthetic"), transport=httpx2.MockTransport(respond)
    )
    result = asyncio.run(
        reviewer.suggest(
            [], 80, ai_instructions="Audience preference", quality_mode=True, max_clips=3
        )
    )
    assert len(result[0].candidates) == 2


def test_presentation_crop_group_and_text_protection():
    frames = [
        (0.0, np.full((216, 384, 3), 90, np.uint8)),
        (1.0, np.full((216, 384, 3), 95, np.uint8)),
    ]
    face = [[(0.60, 0.25, 0.16, 0.20)], [(0.61, 0.25, 0.16, 0.20)]]
    plan = plan_frames(frames, face, 1920, 1080, 0, 2, "auto", "auto", "auto")
    assert plan.smart_framing and plan.smart_framing.strategy == "crop"
    assert plan.caption_position != plan.headline_position
    group = plan_frames(
        frames, [face[0] + [(0.1, 0.25, 0.1, 0.2)]] * 2, 1920, 1080, 0, 2, "podcast", "auto", "auto"
    )
    assert group.smart_framing is None and "participants" in group.summary
    text = frames[0][1].copy()
    cv2.putText(
        text, "IMPORTANT TEXT", (12, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2
    )
    assert text_regions(text)
    protected = plan_frames([(0, text)], [face[0]], 1920, 1080, 0, 2, "auto", "auto", "auto")
    assert protected.smart_framing is None and any("Text-like" in w for w in protected.warnings)
    game = plan_frames(frames, face, 1920, 1080, 0, 2, "gameplay", "auto", "auto")
    assert game.smart_framing is None and "HUD" in game.summary


def test_presentation_cache_and_explicit_edit_override(context, monkeypatch):
    client, manager, recording, _, _ = context
    calls = []

    def analyze(*args):
        calls.append(True)
        return PresentationPlan(summary="Test plan", cover_at=1.0)

    monkeypatch.setattr(manager.presentation, "analyze", analyze)
    # The media adapter explicitly accepts the new bounded cover extraction call.
    monkeypatch.setattr(
        manager.media,
        "thumbnail",
        lambda source, output, at, cancel, **kwargs: output.write_bytes(b"synthetic-image"),
    )
    result = create(client, batch([recording], presentation_mode="auto"))
    assert result["status"] == "ready", result
    item = result["items"][0]
    assert item["presentation_complete"] and len(calls) == 1
    assert item["export"]["quality"]["cover_at"] == 1
    client.post(f"/api/projects/{result['id']}/resume", json={})
    assert finished(client, result["id"])["items"][0]["export_id"] == item["export_id"]
    assert len(calls) == 1
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{item['id']}", json=edit(item, title="Manual title")
    )
    assert response.status_code == 202, response.text
    finished(client, result["id"])
    assert len(calls) == 1


def test_cover_frame_must_remain_in_selected_footage():
    with pytest.raises(ValidationError, match="cover frame"):
        ExportRequest(start=2.0, end=3.0, quality=QualityOptions(cover_at=1.0))


def test_reanalysis_preserves_existing_overlay_placement(context, monkeypatch):
    client, manager, recording, _, _ = context
    result = create(client, batch([recording]))
    item = result["items"][0]
    monkeypatch.setattr(
        manager.presentation,
        "analyze",
        lambda *args: PresentationPlan(
            summary="Test re-analysis", cover_at=1.0, headline_position="centre"
        ),
    )
    monkeypatch.setattr(
        manager.media,
        "thumbnail",
        lambda source, output, at, cancel, **kwargs: output.write_bytes(b"synthetic-image"),
    )
    response = client.patch(
        f"/api/projects/{result['id']}/clips/{item['id']}",
        json=edit(
            item,
            auto_presentation=True,
            overlays=[{"text": "Saved CTA", "start": 0.0, "end": 1.0, "position": "centre"}],
        ),
    )
    assert response.status_code == 202, response.text
    updated = finished(client, result["id"])
    assert updated["status"] == "ready", updated
    assert updated["items"][0]["headline_position"] == "auto"
    assert any("preserved" in warning for warning in updated["items"][0]["presentation_warnings"])


@pytest.mark.parametrize("smart", [False, True])
def test_actual_cover_and_local_analysis_preserve_duration_audio(settings, tmp_path, smart):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=320x180:r=12:d=3,drawbox=color=red:x=0:y=0:w=iw:h=ih:t=fill:enable='gte(t,1)'",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    media = Media(settings)
    info = media.probe(source)
    plan = AutoPresentation(media).analyze(
        source, info, 0, 3, "gameplay", "auto", "auto", threading.Event(), lambda: None
    )
    assert 0 <= plan.cover_at < 3 and plan.smart_framing is None
    cover = tmp_path / "cover.jpg"
    media.thumbnail(source, cover, 1.5, threading.Event(), max_dimension=1920)
    output = tmp_path / "clip.mp4"
    headline = tmp_path / "title.ass"
    captions = tmp_path / "captions.ass"
    write_headline(headline, HeadlineOptions(text="A clear opening"), 3, "lower")
    write_ass([{"start": 0.6, "end": 1.0, "text": "Test captions"}], 0, 3, captions)
    rendered = media.render(
        source,
        output,
        0,
        3,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        cover_path=cover,
        headline_path=headline,
        caption_path=captions,
        quality=QualityOptions(
            cover_at=1.5,
            smart_framing=SmartFraming(
                detector="faces",
                strategy="crop",
                width=0.316,
                height=1.0,
                points=[FramingPoint(at=0.0, x=0.2, y=0.0), FramingPoint(at=3.0, x=0.3, y=0.0)],
            ),
        )
        if smart
        else None,
    )
    assert (rendered.width, rendered.height, rendered.has_audio) == (1080, 1920, True)
    assert rendered.duration == pytest.approx(3, abs=0.1)

    def pixel(at):
        return subprocess.run(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-ss",
                str(at),
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-vf",
                "scale=1:1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout

    first, normal = pixel(0), pixel(0.5)
    assert first[0] > first[2] + 100 and normal[2] > normal[0] + 100
