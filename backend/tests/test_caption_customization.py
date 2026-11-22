"""Saved caption settings and manual timing; inference/provider fixtures are synthetic."""

import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.captions import CaptionCustomization, build_cues, write_ass
from backend.media import Media
from backend.models import CaptionOptions, SelectionDraft
from backend.tests.test_animated_captions import events, text
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import frame_pixels, word
from backend.tests.test_pipeline import await_export
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_projects import context as context
from backend.tests.test_projects import create, finished, payload
from backend.tests.test_transcription import add_recording
from backend.tests.test_transcription import finished as finished_transcript
from backend.tests.test_transcription import manager as manager
from backend.transcription_models import TranscriptionError, WordsPatch


@pytest.mark.parametrize(
    "grouping,count", [("one", 5), ("two", 3), ("three", 2), ("four", 2), ("auto", 1)]
)
def test_grouping_preserves_words_and_actual_boundaries(grouping, count):
    words = [word(10 + i * 0.2, 10.15 + i * 0.2, f"Word{i}") for i in range(5)]
    cues = build_cues(
        words, 10, 11, CaptionCustomization(grouping=grouping, capitalization="upper")
    )
    assert len(cues) == count
    assert " ".join(cue.text for cue in cues) == "WORD0 WORD1 WORD2 WORD3 WORD4"
    assert cues[0].start == 0 and cues[-1].end == pytest.approx(0.95)
    assert all(0 <= cue.start < cue.end <= 1 for cue in cues)
    assert words[0]["text"] == "Word0"


@pytest.mark.parametrize("preset", ["classic", "flow", "glow"])
def test_custom_size_case_and_emphasis_are_in_safe_ass(tmp_path, preset):
    path = tmp_path / "custom.ass"
    write_ass(
        [word(0, 0.4, "Hello"), word(0.5, 1, "world")],
        0,
        1,
        path,
        preset=preset,
        emphasis_words=["hello"],
        customization=CaptionCustomization(font_size=88, capitalization="lower", grouping="one"),
    )
    ass = path.read_text(encoding="utf-8-sig")
    assert "Style: Default,Arial,88," in ass
    assert [text(event) for event in events(path)] == ["hello", "world"]
    assert r"\b1" in ass


@pytest.mark.parametrize(
    "invalid",
    [
        {"font_size": 31},
        {"font_size": 97},
        {"font_size": True},
        {"grouping": "sentence"},
        {"capitalization": "inject"},
        {"unknown": 1},
    ],
)
def test_reject_invalid_customization(invalid):
    with pytest.raises(ValidationError):
        CaptionCustomization(**invalid)


def test_legacy_defaults():
    assert (
        CaptionOptions(transcription_id=uuid4(), revision=1).customization == CaptionCustomization()
    )
    assert SelectionDraft(start=0.0, end=5.0).caption_customization == CaptionCustomization()


def test_project_customization_persists_and_only_rerenders_affected_clip(context):
    client, service, recording, provider, _ = context
    options = {"grouping": "two", "capitalization": "upper", "font_size": 80}
    result = create(client, payload(recording, caption_customization=options))
    assert result["status"] == "ready", result
    first, other = result["items"]
    assert (
        first["export"]["captions"]["customization"] == CaptionCustomization(**options).model_dump()
    )
    original = service.store.clip(first["export_id"])
    renders, calls = len(service.media.renders), len(provider.calls)
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
    edit["caption_customization"] = {**options, "grouping": "one"}
    response = client.patch(f"/api/projects/{result['id']}/clips/{first['id']}", json=edit)
    assert response.status_code == 202, response.text
    updated = finished(client, result["id"])
    assert updated["status"] == "ready", updated
    assert updated["items"][0]["export"]["captions"]["customization"]["grouping"] == "one"
    assert updated["items"][1]["export_id"] == other["export_id"]
    assert len(service.media.renders) == renders + 1 and len(provider.calls) == calls
    assert service.store.clip(first["export_id"]) == original
    assert service.records.get(result["id"]).items[0].caption_customization.grouping == "one"


def test_timing_restore_cache_isolation_and_revision(manager):
    recording = add_recording(manager.store)
    job = finished_transcript(manager, manager.submit(recording.id))
    corrected = WordsPatch(
        revision=1, updates=[{"id": 0, "text": "Hello", "start": 0.5, "end": 1.2}]
    )
    assert manager.patch_words(job.id, corrected).revision == 2
    saved = manager.words(job.id, 0.4, 0.8).words[0]
    assert (
        saved.start,
        saved.end,
        saved.original_start,
        saved.original_end,
        saved.timing_edited,
    ) == (0.5, 1.2, 1, 1.4, True)
    snapshot = manager.export_words(recording.id, job.id, 2, 0, 2)
    with pytest.raises(TranscriptionError, match="another session"):
        manager.patch_words(job.id, corrected)
    assert manager.patch_words(job.id, corrected.model_copy(update={"revision": 2})).revision == 2
    reused = finished_transcript(manager, manager.submit(recording.id))
    assert manager.words(reused.id, 0, 2).words[0].start == 0.5
    duplicate = finished_transcript(manager, manager.submit(add_recording(manager.store).id))
    assert manager.words(duplicate.id, 0, 2).words[0].start == 1
    restored = manager.patch_words(
        reused.id,
        WordsPatch(revision=2, updates=[{"id": 0, "text": "Hello", "start": 1.0, "end": 1.4}]),
    )
    assert restored.revision == 3
    assert not manager.words(reused.id, 0, 2).words[0].timing_edited
    assert snapshot[0]["start"] == 0.5


def test_invalid_timing_rolls_back_complete_batch(manager):
    recording = add_recording(manager.store)
    job = finished_transcript(manager, manager.submit(recording.id))
    for start, end, message in [(1.5, 1.8, "transcript order"), (0.5, 8.1, "inside the recording")]:
        with pytest.raises(TranscriptionError, match=message):
            manager.patch_words(
                job.id,
                WordsPatch(
                    revision=1,
                    updates=[
                        {"id": 1, "text": "changed"},
                        {"id": 0, "text": "edited", "start": start, "end": end},
                    ],
                ),
            )
        page = manager.words(job.id, 0, 8)
        assert page.revision == 1 and [w.text for w in page.words] == ["Hello", "world."]
        assert page.words[0].start == 1 and not page.words[0].timing_edited
    # Simultaneous neighbors move as a batch; original ASR overlaps remain supported.
    assert (
        manager.patch_words(
            job.id,
            WordsPatch(
                revision=1,
                updates=[
                    {"id": 0, "text": "Hello", "start": 2.0, "end": 3.0},
                    {"id": 1, "text": "world.", "start": 2.5, "end": 3.2},
                ],
            ),
        ).revision
        == 2
    )


@pytest.mark.parametrize(
    "timing",
    [
        {"start": 1.0},
        {"end": 2.0},
        {"start": 2.0, "end": 1.0},
        {"start": float("nan"), "end": 2.0},
        {"start": True, "end": 2.0},
    ],
)
def test_reject_invalid_timing_contract(timing):
    with pytest.raises(ValidationError):
        WordsPatch(revision=1, updates=[{"id": 0, "text": "hello", **timing}])


def test_timing_api_exports_snapshot_and_draft_reopens(context):
    client, service, recording, _, _ = context
    job = service.transcriptions.transcripts.latest(recording.id)
    words_url = f"/api/transcriptions/{job.id}/words"
    patched = client.patch(
        words_url,
        json={
            "revision": 1,
            "updates": [
                {"id": 0, "text": "Evidence 0.", "start": 0.5, "end": 3.5},
            ],
        },
    )
    assert patched.status_code == 200, patched.text
    options = {"grouping": "one", "capitalization": "upper", "font_size": 72}
    draft_url = f"/api/recordings/{recording.id}/draft"
    draft = client.get(draft_url).json()
    draft["caption_customization"] = options
    saved = client.put(draft_url, json=draft)
    assert saved.status_code == 200, saved.text
    assert (
        client.get(draft_url).json()["caption_customization"]
        == CaptionCustomization(**options).model_dump()
    )
    exported = client.post(
        f"/api/recordings/{recording.id}/clips",
        json={
            "start": 0.0,
            "end": 4.0,
            "captions": {"transcription_id": job.id, "revision": 2, "customization": options},
        },
    )
    assert exported.status_code == 202, exported.text
    clip_id = exported.json()["id"]
    restored = client.patch(
        words_url,
        json={
            "revision": 2,
            "updates": [
                {"id": 0, "text": "Evidence 0.", "start": 0.0, "end": 4.0},
            ],
        },
    )
    assert restored.status_code == 200
    assert await_export(client, clip_id)["status"] == "succeeded"
    snapshot = service.store.caption_words(clip_id)[0]
    assert (snapshot["start"], snapshot["end"]) == (0.5, 3.5)
    assert service.store.clip(clip_id).captions.customization.font_size == 72
    assert client.get(words_url + "?start=0&end=4").json()["words"][0]["start"] == 0


def test_real_customized_export_keeps_audio_and_timing(tmp_path, caption_media):
    settings, source = caption_media
    subtitles, output = tmp_path / "custom.ass", tmp_path / "custom.mp4"
    write_ass(
        [word(1.2, 1.5, "Hello"), word(1.7, 2, "World")],
        1,
        2.3,
        subtitles,
        customization=CaptionCustomization(grouping="one", capitalization="upper", font_size=80),
    )
    info = Media(settings).render(
        source,
        output,
        1,
        2.3,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        caption_path=subtitles,
    )
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    assert info.duration == pytest.approx(1.3, abs=0.1)
    assert [text(event) for event in events(subtitles)] == ["HELLO", "WORLD"]
    before = frame_pixels(settings, output, 0.05)
    during = frame_pixels(settings, output, 0.35)
    gap = frame_pixels(settings, output, 0.6)
    assert max(before) < 40 and max(gap) < 40 and max(during) > 180
