import subprocess
import threading
from dataclasses import replace

import pytest

from backend.captions import CaptionCue, CaptionError, build_cues, write_ass
from backend.config import Settings
from backend.media import Media, MediaError, RenderCancelled


def word(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_source_words_are_intersected_rebased_and_edits_preserved():
    words = [
        word(9, 9.5, "outside"),
        word(9.8, 10.2, "Corrected"),
        word(10.25, 10.5, ""),
        word(10.5, 11, "text."),
        word(11.9, 12.5, "boundary"),
        word(12.5, 13, "outside"),
    ]
    assert build_cues(words, 10, 12) == [
        CaptionCue(0, 1, "Corrected text."),
        CaptionCue(pytest.approx(1.9), 2, "boundary"),
    ]
    assert build_cues(words, 10.6, 11.5) == [CaptionCue(0, pytest.approx(0.4), "text.")]
    assert words[1] == word(9.8, 10.2, "Corrected")


def test_grouping_respects_pause_punctuation_size_and_retains_every_word():
    words = [word(index * 0.25, index * 0.25 + 0.2, f"word{index}") for index in range(8)]
    words.extend([word(3, 3.2, "Stop."), word(3.3, 3.5, "Then"), word(3.6, 3.8, "go!")])
    cues = build_cues(words, 0, 4)
    assert all(len(cue.text) <= 48 and len(cue.text.split()) <= 6 for cue in cues)
    assert " ".join(cue.text for cue in cues) == " ".join(item["text"] for item in words)
    assert cues[0].text == "word0 word1 word2 word3 word4 word5"
    assert cues[1].end == 1.95
    assert cues[2].start == 3


def test_overlapping_asr_words_keep_their_union_and_all_text():
    words = [word(1, 2, "long"), word(1.2, 1.4, "overlap"), word(1.6, 1.8, "word")]
    assert build_cues(words, 0, 3) == [CaptionCue(1, 2, "long overlap word")]
    words = [word(1 + index * 0.05, 2, f"w{index}") for index in range(8)]
    cues = build_cues(words, 0, 3)
    assert [cue.text for cue in cues] == ["w0 w1 w2 w3 w4 w5", "w6 w7"]
    assert [(cue.start, cue.end) for cue in cues] == [(1, 2), (1.3, 2)]


@pytest.mark.parametrize(
    "words,start,end",
    [
        ([word(1, 2, "later"), word(0.5, 1, "earlier")], 0, 3),
        ([word(1, 1, "zero")], 0, 3),
        ([word(-1, 1, "negative")], 0, 3),
        ([word(True, 1, "boolean")], 0, 3),
        ([word(0, float("inf"), "infinite")], 0, 3),
        ([word(0, 1, "x" * 49)], 0, 3),
        ([word(0, 1, None)], 0, 3),
        ([None], 0, 3),
        ([], float("nan"), 3),
        ([], 10**400, 3),
        ([], 3, 2),
    ],
)
def test_invalid_caption_inputs_fail_explicitly(words, start, end):
    with pytest.raises(CaptionError):
        build_cues(words, start, end)


def test_ass_escapes_untrusted_text_and_never_overwrites(tmp_path):
    path = tmp_path / "safe.ass"
    words = [word(10.2, 11, r"{\pos(0,0)}\Nhello" + "\nDialogue:\x00")]
    assert write_ass(words, 10, 12, path) == 1
    document = path.read_text(encoding="utf-8-sig")
    events = [line for line in document.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 1
    event = events[0]
    assert "0:00:00.20,0:00:01.00" in event
    assert "\\pos" not in event and "{\\" not in event
    assert "｛＼pos(0,0)｝＼Nhello" in event
    assert "\x00" not in document
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_ass(words, 10, 12, path)
    assert path.read_bytes() == original


def test_unrenderable_tiny_phrase_is_not_silently_dropped(tmp_path):
    path = tmp_path / "tiny.ass"
    with pytest.raises(CaptionError, match="too short"):
        write_ass([word(0.001, 0.009, "important")], 0, 1, path)
    assert not path.exists()


def test_single_long_correction_uses_two_lines_without_losing_text(tmp_path):
    path = tmp_path / "long.ass"
    text = "W" * 48
    write_ass([word(0, 1, text)], 0, 1, path)
    event = next(
        line
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.startswith("Dialogue:")
    )
    assert event.count("\\N") == 1
    assert event.endswith("W" * 24 + "\\N" + "W" * 24)
    assert "{\\fs35}" in event


@pytest.fixture(scope="module")
def caption_media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("caption-media") / "input [part]; quotation's"
    folder.mkdir()
    settings = Settings.load()
    source = folder / "source [A], friend's.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=320x180:rate=20:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return settings, source


def frame_pixels(settings, output, timestamp):
    result = subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-ss",
            str(timestamp),
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert len(result.stdout) == 1080 * 1920
    return result.stdout


@pytest.mark.parametrize(
    "position,mode,row_bounds",
    [("lower", "pad", (1350, 1600)), ("centre", "crop", (850, 1050)), ("upper", "pad", (300, 550))],
)
def test_real_burn_in_has_correct_source_timing_position_audio_and_safe_paths(
    tmp_path,
    caption_media,
    position,
    mode,
    row_bounds,
):
    settings, source = caption_media
    folder = tmp_path / "output [A], friend's"
    folder.mkdir()
    captions = folder / "caption [#], friend's.ass"
    output = folder / "export [A], friend's.mp4"
    write_ass([word(3, 3.8, "Caption timing verified")], 2, 5, captions, position)
    exported = Media(settings).render(
        source,
        output,
        2,
        5,
        mode,
        threading.Event(),
        folder / "render.log",
        caption_path=captions,
    )
    assert (exported.width, exported.height) == (1080, 1920)
    assert exported.duration == pytest.approx(3, abs=0.1)
    assert exported.has_audio
    assert not any(pixel > 100 for pixel in frame_pixels(settings, output, 0.5))
    pixels = frame_pixels(settings, output, 1.4)
    lit = [index for index, pixel in enumerate(pixels) if pixel > 180]
    assert len(lit) > 1000, "The downloaded MP4 must contain actual caption pixels."
    assert row_bounds[0] < min(lit) // 1080 < max(lit) // 1080 < row_bounds[1]
    assert min(index % 1080 for index in lit) > 80
    assert max(index % 1080 for index in lit) < 1000
    assert not any(pixel > 100 for pixel in frame_pixels(settings, output, 2.2))
    assert not list(folder.glob("clipping-captions-*"))
    assert captions.exists(), "Rendering must preserve its caller-owned snapshot."


def test_caption_render_failure_retains_log_and_cleans_workdir(tmp_path, caption_media):
    settings, source = caption_media
    captions = tmp_path / "invalid.ass"
    captions.write_text("not an ASS document", encoding="utf-8")
    with pytest.raises(MediaError, match="Export failed"):
        Media(settings).render(
            source,
            tmp_path / "failed.mp4",
            2,
            4,
            "pad",
            threading.Event(),
            tmp_path / "failed.log",
            caption_path=captions,
        )
    assert (tmp_path / "failed.log").stat().st_size > 0
    assert not list(tmp_path.glob("clipping-captions-*"))


def test_caption_render_cancellation_terminates_ffmpeg_and_cleans_workdir(
    tmp_path,
    caption_media,
    monkeypatch,
):
    settings, source = caption_media
    captions = tmp_path / "cancel.ass"
    write_ass([word(1, 3, "Cancel safely")], 0, 5, captions)
    cancel = threading.Event()
    original_popen = subprocess.Popen
    spawned = []

    def start_then_cancel(command, *args, **kwargs):
        process = original_popen(command, *args, **kwargs)
        if any("ass=captions.ass" in str(argument) for argument in command):
            spawned.append(process)
            cancel.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", start_then_cancel)
    with pytest.raises(RenderCancelled):
        Media(settings).render(
            source,
            tmp_path / "cancel.mp4",
            0,
            5,
            "pad",
            cancel,
            tmp_path / "cancel.log",
            caption_path=captions,
        )
    assert len(spawned) == 1 and spawned[0].poll() is not None
    assert not list(tmp_path.glob("clipping-captions-*"))


def test_missing_caption_file_fails_instead_of_exporting_without_captions(tmp_path, caption_media):
    settings, source = caption_media
    output = tmp_path / "missing.mp4"
    with pytest.raises(MediaError, match="snapshot is missing"):
        Media(replace(settings, data_dir=tmp_path)).render(
            source,
            output,
            0,
            2,
            "pad",
            threading.Event(),
            tmp_path / "missing.log",
            caption_path=tmp_path / "absent.ass",
        )
    assert not output.exists()


def test_real_untrusted_caption_text_cannot_move_or_add_subtitles(tmp_path, caption_media):
    settings, source = caption_media
    captions = tmp_path / "untrusted.ass"
    write_ass([word(1, 2, r"{\pos(0,0)}\NUntrusted")], 0, 3, captions, "centre")
    output = tmp_path / "untrusted.mp4"
    Media(settings).render(
        source,
        output,
        0,
        3,
        "pad",
        threading.Event(),
        tmp_path / "untrusted.log",
        caption_path=captions,
    )
    pixels = frame_pixels(settings, output, 1.5)
    lit = [index for index, pixel in enumerate(pixels) if pixel > 180]
    assert len(lit) > 1000
    assert 850 < min(lit) // 1080 < max(lit) // 1080 < 1050
    assert min(index % 1080 for index in lit) > 80
    assert max(index % 1080 for index in lit) < 1000


def test_real_changing_clip_start_rebases_partial_word_without_mutation(tmp_path, caption_media):
    settings, source = caption_media
    words = [word(3, 3.8, "Same source word")]
    captions = tmp_path / "shortened.ass"
    write_ass(words, 3.5, 5, captions)
    output = tmp_path / "shortened.mp4"
    Media(settings).render(
        source,
        output,
        3.5,
        5,
        "pad",
        threading.Event(),
        tmp_path / "shortened.log",
        caption_path=captions,
    )
    assert sum(pixel > 180 for pixel in frame_pixels(settings, output, 0.1)) > 1000
    assert not any(pixel > 100 for pixel in frame_pixels(settings, output, 0.7))
    assert words == [word(3, 3.8, "Same source word")]
