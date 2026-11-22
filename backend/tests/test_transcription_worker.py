"""Explicit inference fixture for isolated invalid timing, without running a model."""

import json
import sys
import wave
from types import SimpleNamespace

from backend.transcription_worker import SAMPLE_RATE, transcribe


def test_unusable_word_timing_is_counted_without_losing_valid_transcript(tmp_path, monkeypatch):
    words = [
        SimpleNamespace(start=1, end=1.3, probability=0.9, word="First"),
        SimpleNamespace(start=0.5, end=0.7, probability=0.9, word="backward"),
        SimpleNamespace(start=2, end=2, probability=0.9, word="zero"),
        SimpleNamespace(start=3, end=3.5, probability=0.9, word="last."),
    ]

    class InferenceFixture:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [SimpleNamespace(words=words, end=4)], None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=InferenceFixture)
    )
    with wave.open(str(tmp_path / "audio.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(b"\0\0" * SAMPLE_RATE * 5)
    transcribe(
        {"work_dir": str(tmp_path), "model_dir": "explicit-fixture", "threads": 1, "duration": 5}
    )
    result = [json.loads(line) for line in (tmp_path / "words.ndjson").read_text().splitlines()]
    assert [(word["id"], word["text"], word["start"]) for word in result] == [
        (0, "First", 1),
        (1, "last.", 3),
    ]
    assert json.loads((tmp_path / "complete.json").read_text()) == {
        "word_count": 2,
        "skipped_timing_words": 2,
        "merged_boundary_words": 0,
    }
