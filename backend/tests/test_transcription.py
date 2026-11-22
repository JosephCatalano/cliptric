"""Lifecycle tests use an explicit inference stub; media timing tests use real FFmpeg."""

import hashlib
import json
import subprocess
import sys
import threading
import time
import wave
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.config import TRANSCRIPTION_REPO, Settings
from backend.media import Media, MediaInfo
from backend.models import Recording
from backend.storage import Store, now
from backend.transcription import REQUIRED_MODEL_FILES, Transcriptions, create_transcription_router
from backend.transcription_models import (
    TranscriptionError,
    WordsPatch,
)


class StubMedia:
    def probe(self, path):
        return MediaInfo(8, 320, 180, True, audio_index=1)


class StubInference(Transcriptions):
    """Deterministic worker to exercise persistence and races without pretending to infer speech."""

    def __init__(self, store, media, settings):
        super().__init__(store, media, settings)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.runs = 0
        self.failure = False
        self.result_words = [
            {"id": 0, "start": 1.0, "end": 1.4, "text": "Hello", "probability": 0.9},
            {"id": 1, "start": 1.35, "end": 1.9, "text": "world.", "probability": 0.8},
        ]

    def _model_identity(self, deadline):
        self._check_running(deadline)
        return {"unit_test_model": 1}

    def _cache_key(self, source_hash, model):
        return hashlib.sha256((source_hash + json.dumps(model)).encode()).hexdigest()

    def _decode(self, source, work, audio_index, duration, deadline, log):
        self._check_running(deadline)

    def _process(self, command, work, duration, deadline, log, *, transcription_id=None):
        self.runs += 1
        self.entered.set()
        while not self.release.wait(0.01):
            self._check_running(deadline)
        self._check_running(deadline)
        if self.failure:
            raise TranscriptionError("Deliberate unit-test inference failure.")
        (work / "words.ndjson").write_text(
            "".join(json.dumps(word) + "\n" for word in self.result_words),
            encoding="utf-8",
        )
        (work / "complete.json").write_text(
            json.dumps(
                {
                    "word_count": len(self.result_words),
                    "skipped_timing_words": 0,
                    "merged_boundary_words": 0,
                }
            ),
            encoding="utf-8",
        )


def add_recording(store, *, audio=True, contents=b"unit test source"):
    recording = Recording(
        id=str(uuid4()),
        name="test.mp4",
        duration=8,
        width=320,
        height=180,
        has_audio=audio,
        size_bytes=len(contents),
        created_at=now(),
    )
    store.save_recording(recording, ".mp4")
    store.source_path(recording.id).write_bytes(contents)
    return recording


@pytest.fixture
def manager(tmp_path):
    store = Store(tmp_path / "data")
    settings = replace(Settings.load(), data_dir=store.root, min_free_bytes=0)
    service = StubInference(store, StubMedia(), settings)
    service.initialize()
    try:
        yield service
    finally:
        service.release.set()
        service.close()


def finished(manager, job):
    manager.pool.submit(lambda: None).result(timeout=10)
    assert manager.current() is None
    return manager.transcripts.job(job.id)


def test_success_edits_reopen_cache_and_source_invalidation(manager):
    recording = add_recording(manager.store)
    first = finished(manager, manager.submit(recording.id))
    assert (first.status, first.word_count, first.revision) == ("succeeded", 2, 1)
    page = manager.words(first.id, 1.3, 1.6)
    assert [word.id for word in page.words] == [0, 1]
    changed = manager.patch_words(
        first.id, WordsPatch(revision=1, updates=[{"id": 1, "text": "there."}])
    )
    assert changed.revision == 2
    with pytest.raises(TranscriptionError, match="another session"):
        manager.patch_words(first.id, WordsPatch(revision=1, updates=[{"id": 0, "text": "Hey"}]))
    unchanged = manager.patch_words(
        first.id, WordsPatch(revision=2, updates=[{"id": 1, "text": "there."}])
    )
    assert unchanged.revision == 2
    second = finished(manager, manager.submit(recording.id))
    assert second.status == "succeeded"
    assert manager.runs == 1
    assert manager.words(second.id, 0, 8).words[1].text == "there."
    assert second.revision == 2
    reopened = Transcriptions(manager.store, manager.media, manager.settings)
    try:
        reopened.initialize()
        assert reopened.words(second.id, 0, 8).words[1].edited
    finally:
        reopened.close()
    manager.store.source_path(recording.id).write_bytes(b"changed source")
    third = finished(manager, manager.submit(recording.id))
    assert third.status == "succeeded"
    assert manager.runs == 2
    assert manager.words(third.id, 0, 8).words[1].text == "world."


def test_duplicate_source_reuses_inference_but_not_editorial_changes(manager):
    first_source = add_recording(manager.store)
    first = finished(manager, manager.submit(first_source.id))
    manager.patch_words(first.id, WordsPatch(revision=1, updates=[{"id": 1, "text": ""}]))
    second_source = add_recording(manager.store)
    second = finished(manager, manager.submit(second_source.id))
    assert manager.runs == 1
    assert manager.words(second.id, 0, 8).words[1].text == "world."
    assert second.revision == 1


def test_cancel_busy_shutdown_and_safe_retry(manager):
    first_source = add_recording(manager.store)
    second_source = add_recording(manager.store)
    manager.release.clear()
    first = manager.submit(first_source.id)
    assert manager.entered.wait(5)
    assert manager.submit(first_source.id).id == first.id
    with pytest.raises(TranscriptionError) as conflict:
        manager.submit(second_source.id)
    assert conflict.value.status_code == 409
    assert manager.cancel(first.id).status == "cancelling"
    assert finished(manager, first).status == "cancelled"
    with pytest.raises(TranscriptionError, match="no longer"):
        manager.cancel(first.id)
    manager.release.set()
    assert finished(manager, manager.submit(first_source.id)).status == "succeeded"
    manager.close()
    with pytest.raises(TranscriptionError, match="stopping"):
        manager.submit(first_source.id)


def test_failed_retry_preserves_prior_transcript_and_recovery(manager):
    recording = add_recording(manager.store)
    first = finished(manager, manager.submit(recording.id))
    manager.store.source_path(recording.id).write_bytes(b"changed")
    manager.failure = True
    retry = finished(manager, manager.submit(recording.id))
    assert retry.status == "failed"
    assert manager.words(first.id, 0, 8).revision == 1
    interrupted = manager.transcripts.create(str(uuid4()), recording.id)
    work = manager.work_root / interrupted.id
    work.mkdir()
    (work / "audio.wav").write_bytes(b"interrupted audio")
    manager.recover()
    assert manager.transcripts.job(interrupted.id).status == "failed"
    assert not work.exists()
    assert manager.words(first.id, 0, 8).words


@pytest.mark.parametrize(
    "words",
    [
        [{"id": 0, "start": 1.0, "end": 9.0, "text": "outside", "probability": 1.0}],
        [{"id": 4, "start": 1.0, "end": 2.0, "text": "gap", "probability": 1.0}],
        [{"id": 0, "start": 1.0, "end": 2.0, "text": "", "probability": 1.0}],
        [{"id": 0, "start": 1.0, "end": 2.0, "text": "invalid", "probability": float("nan")}],
    ],
)
def test_invalid_child_output_never_publishes_partial_words(manager, words):
    recording = add_recording(manager.store)
    manager.result_words = [manager.result_words[0], *words]
    job = finished(manager, manager.submit(recording.id))
    assert job.status == "failed"
    with manager.store.connection() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM transcript_words WHERE transcription_id=?", (job.id,)
            ).fetchone()[0]
            == 0
        )


def test_router_validation_and_export_snapshot_revision(manager):
    app = FastAPI()
    app.include_router(create_transcription_router(manager))
    recording = add_recording(manager.store)
    with TestClient(app) as client:
        assert client.get(f"/api/recordings/{recording.id}/transcription").json() is None
        response = client.post(f"/api/recordings/{recording.id}/transcription")
        assert response.status_code == 202
        job = finished(manager, manager.transcripts.job(response.json()["id"]))
        assert client.get("/api/transcriptions/active").json() is None
        assert (
            client.get(
                f"/api/transcriptions/{job.id}/words", params={"start": 0, "end": 8}
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/transcriptions/{job.id}/words", params={"start": "nan", "end": 8}
            ).status_code
            == 422
        )
        assert (
            client.get(
                f"/api/transcriptions/{job.id}/words", params={"start": 0, "end": 121}
            ).status_code
            == 422
        )
        assert (
            client.patch(
                f"/api/transcriptions/{job.id}/words",
                json={"revision": 1, "updates": [{"id": 0, "text": "X" * 49}]},
            ).status_code
            == 422
        )
        assert (
            client.patch(
                f"/api/transcriptions/{job.id}/words",
                json={"revision": 1, "updates": [{"id": 0, "text": "X"}, {"id": 0, "text": "Y"}]},
            ).status_code
            == 422
        )
        changed = client.patch(
            f"/api/transcriptions/{job.id}/words",
            json={"revision": 1, "updates": [{"id": 0, "text": "Hey"}]},
        )
        assert changed.status_code == 200
        assert changed.json()["revision"] == 2
        assert (
            client.patch(
                f"/api/transcriptions/{job.id}/words",
                json={"revision": 1, "updates": [{"id": 0, "text": "Hi"}]},
            ).status_code
            == 409
        )
        assert client.get(f"/api/recordings/{uuid4()}/transcription").status_code == 404
        silent = add_recording(manager.store, audio=False)
        assert client.post(f"/api/recordings/{silent.id}/transcription").status_code == 422
    with pytest.raises(HTTPException) as mismatch:
        manager.export_words(recording.id, job.id, 1, 0, 8)
    assert mismatch.value.status_code == 409
    with pytest.raises(HTTPException) as wrong_source:
        manager.export_words(silent.id, job.id, 2, 0, 8)
    assert wrong_source.value.status_code == 404
    snapshot = manager.export_words(recording.id, job.id, 2, 0, 8)
    manager.patch_words(job.id, WordsPatch(revision=2, updates=[{"id": 0, "text": "Later"}]))
    assert snapshot[0]["text"] == "Hey"


def test_parent_storage_error_is_saved_in_processing_log(manager, monkeypatch):
    recording = add_recording(manager.store)

    def denied(*args, **kwargs):
        raise PermissionError(13, "Deliberate local snapshot read failure")

    monkeypatch.setattr(manager, "_process", denied)
    job = finished(manager, manager.submit(recording.id))
    assert job.status == "failed" and "Cannot access local" in job.error
    log = (manager.store.root / "logs" / f"transcription-{job.id}.log").read_text()
    assert "PermissionError" in log and "Deliberate local snapshot read failure" in log


def test_model_manifest_rejects_modified_or_unsafe_model_files(manager, tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    files = {}
    for name in REQUIRED_MODEL_FILES:
        (root / name).write_bytes(b"unit test model file")
        files[name] = hashlib.sha256(b"unit test model file").hexdigest()
    manifest = {
        "repo_id": TRANSCRIPTION_REPO,
        "revision": manager.settings.transcription_model_revision,
        "files": files,
    }
    monkeypatch.setattr(
        "backend.transcription.MODEL_FILES",
        {name: (len(b"unit test model file"), digest) for name, digest in files.items()},
    )
    (root / "clipping-model.json").write_text(json.dumps(manifest), encoding="utf-8")
    service = Transcriptions(
        manager.store, manager.media, replace(manager.settings, transcription_model_dir=root)
    )
    try:
        assert service._model_identity(time.monotonic() + 10) == manifest
        (root / "model.bin").write_bytes(b"modified")
        with pytest.raises(TranscriptionError, match="failed verification"):
            service._model_identity(time.monotonic() + 10)
        manifest["files"]["../outside"] = "a" * 64
        (root / "clipping-model.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(TranscriptionError):
            service._model_identity(time.monotonic() + 10)
    finally:
        service.close()


def test_real_process_timeout_is_reaped(manager, tmp_path, monkeypatch):
    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
    child = []
    popen = subprocess.Popen

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        child.append(process)
        return process

    monkeypatch.setattr("backend.transcription.subprocess.Popen", track)
    with (tmp_path / "process.log").open("wb") as log:
        with pytest.raises(TranscriptionError, match="processing limit"):
            Transcriptions._process(
                manager, [sys.executable, str(script)], tmp_path, 8, time.monotonic() + 0.3, log
            )
    assert len(child) == 1 and child[0].poll() is not None


def test_real_decode_preserves_delayed_audio_origin(manager, tmp_path):
    settings = manager.settings
    source = tmp_path / "delayed.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=size=320x180:rate=24:duration=4",
            "-itsoffset",
            "1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    service = Transcriptions(manager.store, Media(settings), settings)
    work = tmp_path / "decode"
    work.mkdir()
    try:
        info = service.media.probe(source)
        with (work / "decode.log").open("wb") as log:
            service._decode(
                source, work, info.audio_index, info.duration, time.monotonic() + 20, log
            )
        import array

        with wave.open(str(work / "audio.wav"), "rb") as audio:
            assert audio.getframerate() == 16000
            assert abs(audio.getnframes() / 16000 - info.duration) < 0.01
            samples = array.array("h", audio.readframes(audio.getnframes()))
        assert max(abs(value) for value in samples[:16000]) < 8
        assert max(abs(value) for value in samples[28000:32000]) > 500
        assert max(abs(value) for value in samples[48000:]) < 8
    finally:
        service.close()
