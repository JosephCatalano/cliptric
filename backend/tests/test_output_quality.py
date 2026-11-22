"""Reversible editorial/media quality checks; synthetic sources do not prove editorial taste."""

import json
import subprocess
import threading
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.editing import (
    FramingPoint,
    QualityOptions,
    Removal,
    SmartFraming,
    mapped_overlays,
    mapped_words,
    retained_intervals,
)
from backend.framing import SourceRegion
from backend.highlight_models import HighlightRequest, ProposedHighlights
from backend.highlight_selection import Segment, ground_candidates
from backend.main import create_app
from backend.media import Media
from backend.models import ExportRequest
from backend.overlays import TextOverlay
from backend.quality_analysis import merge_removals, visual_plan
from backend.tests.test_pipeline import await_export, import_video
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload


def test_edit_mapping_restores_original_and_maps_captions_overlays():
    options = QualityOptions(removals=[Removal(start=12.0, end=13.0, reason="silence")])
    kept = retained_intervals(10, 16, options)
    assert kept == [(10, 12), (13, 16)]
    words = [
        {"start": 11.0, "end": 11.5, "text": "before"},
        {"start": 12.2, "end": 12.5, "text": "um"},
        {"start": 14.0, "end": 15.0, "text": "after"},
    ]
    mapped = mapped_words(words, kept, 10)
    assert [word["text"] for word in mapped] == ["before", "after"]
    assert mapped[-1]["start"] == 13 and words[-1]["start"] == 14
    overlay = TextOverlay(text="CTA", start=4.0, end=5.0, position="upper")
    assert mapped_overlays([overlay], 10, kept)[0].start == 3
    options.removals[0].enabled = False
    assert retained_intervals(10, 16, options) == [(10, 16)]


@pytest.mark.parametrize("cuts", [[(-1, 2)], [(1, 8)], [(1, 3), (2, 4)], [(0, 4)], [(0.01, 0.5)]])
def test_invalid_edit_plans_fail_safely(cuts):
    with pytest.raises((ValidationError, ValueError)):
        options = QualityOptions(
            removals=[Removal(start=float(a), end=float(b), reason="manual") for a, b in cuts]
        )
        retained_intervals(0, 6, options)


def test_protected_region_cannot_be_excluded():
    with pytest.raises(ValidationError):
        SmartFraming(
            detector="motion",
            strategy="crop",
            width=0.3,
            height=1.0,
            points=[FramingPoint(at=0.0, x=0.0, y=0.0)],
            protected_regions=[SourceRegion(x=0.8, y=0.1, width=0.1, height=0.1)],
        )


def test_visual_analysis_missing_faces_and_protected_hud_preserve_context():
    frames = np.zeros((10, 180, 320, 3), dtype=np.uint8)
    faces = visual_plan(frames, 320, 180, 0, 5, "faces", [])
    assert faces.strategy == "fit" and faces.width == 1
    short = visual_plan(frames[:1], 320, 180, 0, 0.5, "faces", [])
    assert short.strategy == "fit"
    for index, frame in enumerate(frames):
        frame[60:90, 120 + index * 4 : 145 + index * 4] = 255
    motion = visual_plan(
        frames, 320, 180, 0, 5, "motion", [SourceRegion(x=0.0, y=0.8, width=1.0, height=0.2)]
    )
    assert motion.strategy == "fit" and "protected" in " ".join(motion.warnings)
    target = visual_plan(frames, 320, 180, 0, 5, "motion", [])
    assert target.strategy == "crop" and len(target.points) <= 24


def test_removal_merging_does_not_swallow_short_speech_between_proposals():
    cuts = [
        Removal(start=1.0, end=2.0, reason="silence"),
        Removal(start=2.01, end=3.0, reason="silence"),
    ]
    assert len(merge_removals(cuts)) == 2
    with pytest.raises(ValueError):
        retained_intervals(0, 6, QualityOptions(removals=cuts))


def test_quality_mode_requires_supported_hook_context_and_payoff():
    window = [Segment(0, 1, 5, "A useful opening."), Segment(1, 5, 10, "A resolved ending.")]
    base = {
        "first_segment": 0,
        "last_segment": 1,
        "score": 99,
        "title": "Source moment",
        "reason": "Source-grounded example",
        "evidence": "useful opening",
        "category": "insight",
    }
    assessment = {
        "hook": 4,
        "context": 4,
        "payoff": 4,
        "standalone": 4,
        "opening_evidence": "useful opening",
        "ending_evidence": "resolved ending",
    }
    request = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        quality_mode=True,
        min_seconds=8,
        max_seconds=15,
    )
    proposed = ProposedHighlights(candidates=[{**base, "quality": assessment}])
    result, rejected = ground_candidates(proposed, window, request, 20, "test")
    assert not rejected and len(result) == 1 and result[0].score == 80
    assert result[0].start == pytest.approx(0.88) and result[0].end == pytest.approx(10.18)
    for bad in [None, {**assessment, "payoff": 2}, {**assessment, "opening_evidence": "invented"}]:
        result, rejected = ground_candidates(
            ProposedHighlights(candidates=[{**base, "quality": bad}]), window, request, 20, "test"
        )
        assert result == [] and rejected == 1


@pytest.fixture(scope="module")
def quality_media(tmp_path_factory, tools):
    ffmpeg = tools.ffmpeg
    root = tmp_path_factory.mktemp("quality-source")
    source = root / "source.mp4"
    command = [ffmpeg, "-v", "error", "-nostdin"]
    for colour in ("red", "black", "blue"):
        command += ["-f", "lavfi", "-i", f"color={colour}:s=320x180:r=20:d=2"]
    for audio in (
        "sine=f=440:r=48000:d=2",
        "anullsrc=r=48000:cl=mono:d=2",
        "sine=f=880:r=48000:d=2",
    ):
        command += ["-f", "lavfi", "-i", audio]
    command += [
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v];[3:a][4:a][5:a]concat=n=3:v=0:a=1[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(source),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    return source


def test_actual_cut_audio_cleanup_caption_overlay_sync_and_immutable_snapshot(
    settings, quality_media, tmp_path, monkeypatch
):
    import backend.jobs as jobs

    captured = []
    original = jobs.write_ass

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(args[3].read_text(encoding="utf-8-sig"))
        return result

    monkeypatch.setattr(jobs, "write_ass", capture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, quality_media)
        service = client.app.state.transcriptions
        job = service.transcripts.create(str(uuid4()), recording["id"])
        words = [
            {"id": 0, "start": 0.3, "end": 1.5, "text": "Before", "probability": 0.9},
            {"id": 1, "start": 4.2, "end": 5.5, "text": "After", "probability": 0.9},
        ]
        path = tmp_path / "words.ndjson"
        path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
        service.transcripts.publish(job.id, "source", "quality", path, 6, 2)
        analysis = client.post(
            f"/api/recordings/{recording['id']}/quality-plan",
            json={
                "start": 0.0,
                "end": 6.0,
                "transcription_id": job.id,
                "revision": 1,
                "silence": True,
                "fillers": False,
                "detector": "faces",
            },
        )
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["smart_framing"]["strategy"] == "fit"
        assert analysis.json()["removals"] and analysis.json()["output_duration"] < 5
        options = {
            "cleanup": "light",
            "normalize": True,
            "removals": [{"start": 2.2, "end": 3.8, "reason": "silence"}],
        }
        submitted = client.post(
            f"/api/recordings/{recording['id']}/clips",
            json={
                "start": 0.0,
                "end": 6.0,
                "quality": options,
                "captions": {"transcription_id": job.id, "revision": 1},
                "overlays": [{"text": "CTA", "start": 4.3, "end": 5.6, "position": "upper"}],
            },
        )
        assert submitted.status_code == 202, submitted.text
        result = await_export(client, submitted.json()["id"])
        assert result["status"] == "succeeded", result
        assert result["duration"] == pytest.approx(4.4, abs=0.1)
        assert any("0:00:02.60,0:00:03.90" in ass for ass in captured)
        assert result["quality"]["removals"][0]["start"] == 2.2
        assert client.app.state.jobs.store.caption_words(result["id"])[1]["start"] == 4.2
        output = client.app.state.jobs.store.export_path(result["id"])
        info = Media(settings).probe(output)
        assert info.has_audio and (info.width, info.height) == (1080, 1920)
        audio = subprocess.run(
            [
                settings.ffmpeg,
                "-v",
                "error",
                "-i",
                str(output),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-f",
                "f32le",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        samples = np.frombuffer(audio, dtype=np.float32)
        assert len(samples) / 48000 == pytest.approx(4.4, abs=0.1)
        assert (
            np.max(np.abs(samples)) < 1
            and np.sqrt(np.mean(samples[int(2.05 * 48000) : int(2.3 * 48000)] ** 2)) < 0.01
        )
        assert np.sqrt(np.mean(samples[int(3 * 48000) : int(3.2 * 48000)] ** 2)) > 0.02
        for at, expected in [(1.0, 0), (2.8, 2)]:
            pixel = subprocess.run(
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
                    "crop=2:2:iw/2:ih/2,scale=1:1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
            assert pixel[expected] > 150 and pixel[2 if expected == 0 else 0] < 50
        # Source transcript remains in original time and can still produce an unedited export.
        assert service.words(job.id, 0, 6).words[1].start == 4.2


def test_actual_smart_crop_render_and_duration(settings, quality_media, tmp_path):
    options = QualityOptions(
        smart_framing=SmartFraming(
            detector="motion",
            strategy="crop",
            width=0.3125,
            height=1.0,
            points=[
                FramingPoint(at=float(i) * 0.06, x=0.2 + float(i) * 0.003, y=0.0) for i in range(24)
            ],
        )
    )
    output = tmp_path / "tracked.mp4"
    info = Media(settings).render(
        quality_media,
        output,
        0,
        1.5,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        quality=options,
    )
    assert info.has_audio and info.duration == pytest.approx(1.5, abs=0.1)


def test_project_quality_edit_persists_without_repeating_analysis(context):
    client, manager, recording, provider, _ = context
    result = create(client, payload(recording))
    first, other = result["items"]
    old = manager.store.clip(first["export_id"])
    calls = len(provider.calls)
    edit = {
        key: first[key]
        for key in (
            "title",
            "start",
            "end",
            "revision",
            "review",
            "caption_preset",
            "caption_position",
            "emphasis_words",
        )
    }
    edit["quality"] = {"cleanup": "light", "normalize": True}
    response = client.patch(f"/api/projects/{result['id']}/clips/{first['id']}", json=edit)
    assert response.status_code == 202, response.text
    updated = finished(client, result["id"])
    assert updated["status"] == "ready", updated
    assert updated["items"][0]["export"]["quality"]["cleanup"] == "light"
    assert updated["items"][1]["export_id"] == other["export_id"] and len(provider.calls) == calls
    assert manager.store.clip(old.id) == old


def test_edits_cannot_silently_drop_a_required_overlay():
    with pytest.raises(ValidationError, match="removes a text overlay"):
        ExportRequest(
            start=0.0,
            end=6.0,
            quality=QualityOptions(removals=[Removal(start=2.0, end=3.0, reason="manual")]),
            overlays=[TextOverlay(text="CTA", start=2.1, end=2.9, position="upper")],
        )


def test_quality_scratch_recovery_only_removes_owned_artifacts(context):
    from backend.quality_analysis import QualityAnalysis

    _, manager, _, _, _ = context
    analysis = QualityAnalysis(manager.store, manager.media, manager.transcriptions)
    folder = analysis.root / "analysis-interrupted"
    folder.mkdir()
    (folder / "frames.rgb").write_bytes(b"owned scratch")
    analysis.initialize()
    assert not folder.exists()
    unknown = analysis.root / "analysis-unknown"
    unknown.mkdir()
    (unknown / "user-file").write_text("preserve", encoding="utf-8")
    with pytest.raises(OSError):
        analysis.initialize()
    assert (unknown / "user-file").read_text() == "preserve"
