"""Resource lifecycle and visible fallback tests; no provider calls or speed claims."""

import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.encoding import encoding_profile
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.transcription_worker import InferenceRuntime
from backend.warm_transcription import WarmTranscription


def test_encoder_fallback_is_visible_and_explicit_gpu_fails(settings, monkeypatch):
    monkeypatch.setattr("backend.encoding.probe_nvenc", lambda *args: False)
    result = encoding_profile(settings)
    assert result.backend == "cpu" and "Used CPU" in result.warning
    with pytest.raises(ValueError, match="NVENC could not initialize"):
        encoding_profile(replace(settings, render_encoder="nvenc"))
    assert encoding_profile(replace(settings, render_encoder="cpu")).warning is None


def test_speech_auto_falls_back_once_and_keeps_model_warm(monkeypatch):
    calls = []

    def model(*args, **kwargs):
        calls.append(kwargs["device"])
        if kwargs["device"] == "cuda":
            raise RuntimeError("Unavailable CUDA runtime fixture")
        return object()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=model))
    monkeypatch.setitem(
        sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1)
    )
    request = {"model_dir": "explicit-fixture", "threads": 1, "device": "auto"}
    runtime = InferenceRuntime()
    first, profile = runtime.load(request)
    assert profile["device"] == "cpu" and profile["warning"]
    assert runtime.load(request)[0] is first and calls == ["cuda", "cpu"]
    with pytest.raises(RuntimeError):
        InferenceRuntime().load({**request, "device": "cuda"})


def test_idle_expiry_cannot_kill_a_new_active_request(tmp_path, monkeypatch):
    timers = []
    processes = []

    class Timer:
        def __init__(self, seconds, callback):
            self.callback = callback
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    class Process:
        def __init__(self, *args, **kwargs):
            self.stdin = SimpleNamespace(
                write=lambda data: None, flush=lambda: None, close=lambda: None
            )
            self.code = None
            processes.append(self)

        def poll(self):
            return self.code

        def kill(self):
            self.code = -9

        def wait(self):
            return self.code

    monkeypatch.setattr("backend.warm_transcription.threading.Timer", Timer)
    monkeypatch.setattr("backend.warm_transcription.subprocess.Popen", Process)
    worker = WarmTranscription(tmp_path, 120)
    first = worker.start(tmp_path / "one.json")
    worker.mark_idle()
    expired = timers[-1]
    assert worker.start(tmp_path / "two.json") is first
    expired.callback()  # A timer already waiting for the lock must be harmless.
    assert first.poll() is None and len(processes) == 1
    worker.mark_idle()
    timers[-1].callback()
    assert first.poll() == -9
    assert worker.start(tmp_path / "three.json") is not first
    worker.close()
