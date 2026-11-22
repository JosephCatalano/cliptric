"""Timed subtitle contracts and actual exported animation; no UI screenshots or AI."""

import re
import subprocess
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

import backend.captions as captions
from backend.captions import CaptionError, build_cues, clean_emphasis, write_ass
from backend.media import Media
from backend.models import CaptionOptions
from backend.project_models import ProjectSettings
from backend.tests.test_captions import caption_media as caption_media
from backend.tests.test_captions import frame_pixels, word


def events(path):
    return [
        line.split(",", 9)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith("Dialogue:")
    ]


def text(event):
    return re.sub(r"\{[^}]*\}", "", event[9]).replace(r"\N", " ")


@pytest.mark.parametrize("preset,marker", [("flow", r"\1c&H0000EFFF&"), ("glow", r"\blur2")])
def test_word_animation_preserves_boundaries_pauses_text_and_clamping(tmp_path, preset, marker):
    words = [word(9.8, 10.4, "Alpha"), word(10.6, 11.2, "Omega")]
    path = tmp_path / "animated.ass"
    assert write_ass(words, 10, 11, path, preset=preset) == 3
    result = events(path)
    assert [(e[1], e[2]) for e in result] == [
        ("0:00:00.00", "0:00:00.40"),
        ("0:00:00.40", "0:00:00.60"),
        ("0:00:00.60", "0:00:01.00"),
    ]
    assert all(text(e) == "Alpha Omega" for e in result)
    assert marker in result[0][9] and marker not in result[1][9] and marker in result[2][9]
    cue = build_cues(words, 10, 11)[0]
    assert [(w.start, w.end) for w in cue.words] == [
        (0, pytest.approx(0.4)),
        (pytest.approx(0.6), 1),
    ]


def test_overlapping_spoken_words_can_both_glow_without_shortening(tmp_path):
    path = tmp_path / "overlap.ass"
    write_ass([word(0, 0.4, "Alpha"), word(0.2, 0.8, "Beta")], 0, 1, path, preset="glow")
    result = events(path)
    assert [(e[1], e[2]) for e in result] == [
        ("0:00:00.00", "0:00:00.20"),
        ("0:00:00.20", "0:00:00.40"),
        ("0:00:00.40", "0:00:00.80"),
    ]
    assert [e[9].count(r"\blur2") for e in result] == [1, 2, 1]


def test_sub_centisecond_words_keep_text_without_invented_highlights(tmp_path):
    path = tmp_path / "tiny.ass"
    write_ass(
        [word(0, 0.4, "Alpha"), word(0.4001, 0.4002, "tiny"), word(0.6, 1, "Omega")],
        0,
        1,
        path,
        preset="flow",
    )
    result = events(path)
    assert all("tiny" in text(e) for e in result)
    assert all(e[1] < e[2] for e in result)
    assert r"\t(" not in result[1][9]


@pytest.mark.parametrize("preset", ["classic", "bold", "boxed", "flow", "glow"])
def test_emphasis_matches_case_punctuation_and_individual_corrected_words(tmp_path, preset):
    path = tmp_path / "emphasis.ass"
    write_ass(
        [word(0, 1, "This is AMAZING!")], 0, 1, path, preset=preset, emphasis_words=["amazing"]
    )
    result = events(path)
    assert all(text(e) == "This is AMAZING!" for e in result)
    assert all(re.search(r"\{\\b1\\1c&H0000EFFF&\}(?:\{[^}]*\})*AMAZING!", e[9]) for e in result)
    assert all(r"{\b1\1c&H0000EFFF&}This" not in e[9] for e in result)
    assert ",0,0,0,0,100,100" in path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("preset", ["flow", "glow"])
def test_animated_layout_escapes_override_injection_and_preserves_wrapping(tmp_path, preset):
    path = tmp_path / "safe.ass"
    write_ass(
        [word(0, 0.5, r"{\pos(0,0)}\Nunsafe"), word(0.5, 1, "W" * 48)], 0, 1, path, preset=preset
    )
    result = events(path)
    assert all(r"\pos(0,0)" not in e[9] for e in result)
    assert any("｛＼pos" in text(e) for e in result)
    assert any(r"\N" in e[9] for e in result)
    assert all(r"\fs" in e[9] for e in result)


@pytest.mark.parametrize(
    "values", [["two words"], ["x" * 49], ["!"], ["bad\x00word"], ["bad\ud800word"], ["word"] * 21]
)
def test_invalid_emphasis_rejected_before_export(values):
    with pytest.raises((ValidationError, CaptionError)):
        CaptionOptions(transcription_id=uuid4(), revision=0, preset="flow", emphasis_words=values)


def test_old_settings_and_new_caption_options_round_trip():
    assert ProjectSettings(analysis_end=20.0).emphasis_words == []
    assert CaptionOptions(transcription_id=uuid4(), revision=0).preset == "classic"
    assert clean_emphasis(["Amazing!", "amazing", "Burger"]) == ["Amazing!", "Burger"]
    value = CaptionOptions(
        transcription_id=uuid4(), revision=0, preset="glow", emphasis_words=["AMAZING", "burger"]
    )
    assert CaptionOptions.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("preset", ["classic", "flow", "glow"])
def test_escaped_fullwidth_glyphs_are_measured_before_font_fitting(tmp_path, preset):
    path = tmp_path / "wide.ass"
    write_ass([word(0, 1, "\\" * 48)], 0, 1, path, preset=preset)
    result = events(path)
    assert all(r"\fs35" in event[9] for event in result)
    assert all(text(event).count("＼") == 48 for event in result)


def test_animation_density_fails_before_creating_a_partial_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(captions, "MAX_ANIMATED_EVENTS", 1)
    path = tmp_path / "bounded.ass"
    with pytest.raises(CaptionError, match="too dense"):
        write_ass([word(0, 0.4, "Alpha"), word(0.6, 1, "Omega")], 0, 1, path, preset="flow")
    assert not path.exists()


def rgb_frame(settings, path, timestamp):
    result = subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert len(result.stdout) == 1080 * 1920 * 3
    return result.stdout


def accent_columns(frame, preset):
    columns = []
    # Captions are in the lower safe region; source is deliberately solid black.
    for pixel in range(1300 * 1080, 1650 * 1080):
        r, g, b = frame[pixel * 3 : pixel * 3 + 3]
        matches = (
            (r > 180 and g > 140 and b < 100)
            if preset == "flow"
            else (b > 100 and b > g + 15 and r > g + 5 and b > r + 10)
        )
        if matches:
            columns.append(pixel % 1080)
    return columns


@pytest.mark.parametrize("preset", ["flow", "glow"])
def test_real_export_moves_accent_with_speech_and_preserves_audio(tmp_path, caption_media, preset):
    settings, source = caption_media
    subtitle, output = tmp_path / "words.ass", tmp_path / "animated.mp4"
    write_ass(
        [word(1.1, 1.65, "Alpha"), word(1.85, 2.4, "Omega")],
        1,
        2.7,
        subtitle,
        preset=preset,
        emphasis_words=["Omega"] if preset == "glow" else [],
    )
    info = Media(settings).render(
        source,
        output,
        1,
        2.7,
        "pad",
        threading.Event(),
        tmp_path / "render.log",
        caption_path=subtitle,
    )
    assert (info.width, info.height) == (1080, 1920) and info.has_audio
    assert info.duration == pytest.approx(1.7, abs=0.1)
    first = accent_columns(rgb_frame(settings, output, 0.35), preset)
    gap = accent_columns(rgb_frame(settings, output, 0.75), preset)
    second = accent_columns(rgb_frame(settings, output, 1.1), preset)
    assert len(first) > 30 and len(second) > 30, (
        "The export must contain actual animated colour/glow pixels."
    )
    assert sum(first) / len(first) + 60 < sum(second) / len(second), (
        "The accent must move to the next spoken word."
    )
    assert len(gap) < min(len(first), len(second)) * 0.1, (
        "Word accent must turn off during the real speech pause."
    )
    assert max(frame_pixels(settings, output, 0.05)) <= 100
    assert max(frame_pixels(settings, output, 1.55)) <= 100
