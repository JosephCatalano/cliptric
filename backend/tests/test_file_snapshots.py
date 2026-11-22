"""Real Windows lock conflicts and strict failure checks for atomic worker state."""

import errno
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.files import read_json_snapshot, write_json_atomic
from backend.transcription_worker import write_json as write_transcription
from backend.youtube_worker import write_json as write_youtube


@pytest.mark.skipif(os.name != "nt", reason="Windows open-file replacement semantics")
@pytest.mark.parametrize("publish", [write_transcription, write_youtube])
def test_publication_recovers_after_real_reader_lock(tmp_path, monkeypatch, publish):
    path = tmp_path / "progress.json"
    publish(path, {"seconds": 1})
    conflict = threading.Event()
    replace = Path.replace

    def observe_conflict(source, destination):
        try:
            return replace(source, destination)
        except PermissionError:
            conflict.set()
            raise

    monkeypatch.setattr(Path, "replace", observe_conflict)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with path.open("rb") as previous:
            pending = executor.submit(publish, path, {"seconds": 2})
            assert conflict.wait(timeout=2)
            assert json.load(previous) == {"seconds": 1}
        pending.result(timeout=2)
    assert json.loads(path.read_text()) == {"seconds": 2}
    assert not path.with_suffix(".json.writing").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows open-file replacement semantics")
def test_persistent_lock_fails_without_corrupting_previous_state(tmp_path):
    path = tmp_path / "progress.json"
    write_json_atomic(path, {"seconds": 1})
    with path.open("rb") as previous:
        with pytest.raises(PermissionError):
            write_json_atomic(path, {"seconds": 2})
        assert json.load(previous) == {"seconds": 1}
    assert json.loads(path.read_text()) == {"seconds": 1}


def test_unrelated_permission_error_is_not_retried(tmp_path, monkeypatch):
    calls = 0

    def denied(source, destination):
        nonlocal calls
        calls += 1
        raise PermissionError(errno.EACCES, "Deliberate non-sharing permission error")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(PermissionError, match="non-sharing"):
        write_json_atomic(tmp_path / "progress.json", {"seconds": 1})
    assert calls == 1


def test_write_failure_is_not_retried_or_silenced(tmp_path, monkeypatch):
    calls = 0

    def full(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError(errno.ENOSPC, "Deliberate full disk")

    monkeypatch.setattr(Path, "write_text", full)
    with pytest.raises(OSError) as failure:
        write_json_atomic(tmp_path / "progress.json", {"seconds": 1})
    assert failure.value.errno == errno.ENOSPC
    assert calls == 1


def test_nonfinite_state_never_replaces_valid_snapshot(tmp_path):
    path = tmp_path / "progress.json"
    write_json_atomic(path, {"seconds": 1})
    with pytest.raises(ValueError):
        write_json_atomic(path, {"seconds": float("nan")})
    assert json.loads(path.read_text()) == {"seconds": 1}


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing errors")
def test_snapshot_reader_retries_only_transient_windows_conflicts(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    write_json_atomic(path, {"seconds": 1})
    original = Path.open
    calls = 0

    def transient(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError("Deliberate pending Windows replacement")
            error.winerror = 5
            raise error
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", transient)
    assert read_json_snapshot(path) == {"seconds": 1} and calls == 2

    def persistent(*args, **kwargs):
        error = PermissionError("Deliberate persistent Windows lock")
        error.winerror = 32
        raise error

    monkeypatch.setattr(Path, "open", persistent)
    with pytest.raises(PermissionError, match="persistent"):
        read_json_snapshot(path)

    calls = 0

    def crt_conflict(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(errno.EACCES, "Deliberate CRT sharing conflict")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", crt_conflict)
    assert read_json_snapshot(path) == {"seconds": 1} and calls == 2

    def unrelated(*args, **kwargs):
        raise PermissionError(errno.EPERM, "Deliberate unrelated access denial")

    monkeypatch.setattr(Path, "open", unrelated)
    with pytest.raises(PermissionError, match="unrelated"):
        read_json_snapshot(path)


def test_concurrent_snapshot_publication_and_reading(tmp_path):
    path = tmp_path / "progress.json"
    write_json_atomic(path, {"seconds": 0})

    def publish():
        for value in range(200):
            write_json_atomic(path, {"seconds": value})
            time.sleep(0.001)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(publish)
        while not pending.done():
            value = read_json_snapshot(path)
            assert value is None or 0 <= value["seconds"] < 200
            time.sleep(0.001)
        pending.result(timeout=5)
    assert read_json_snapshot(path) == {"seconds": 199}


def test_missing_invalid_and_oversized_snapshots(tmp_path):
    path = tmp_path / "progress.json"
    assert read_json_snapshot(path) is None
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_json_snapshot(path)
    path.write_text(" " * 4097, encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        read_json_snapshot(path)
    path.write_text("null", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        read_json_snapshot(path)
