"""Synthetic narrative/provider cases and real FFmpeg source timing checks."""

import json
import subprocess
from collections import Counter
from dataclasses import replace
from threading import Event
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from backend.editorial_review import EditorialProposal, EditorialReviewer, ground_review, shortlist
from backend.highlight_context import build_context, ground_narrative, nearby_context
from backend.highlight_media import MediaSampler, acoustic_cues, audio_levels, sample_ranges
from backend.highlight_models import HighlightRequest, NarrativeNote, ProposedHighlights
from backend.highlight_selection import Segment, ground_candidates, overlaps
from backend.main import create_app
from backend.media import Media
from backend.tests.test_highlights import finished, request
from backend.tests.test_highlights import service as service
from backend.tests.test_pipeline import import_video
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_project_media import completed


def output(payload):
    return httpx2.Response(
        200,
        json={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(payload)}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


def proposal(first, last, evidence, **changes):
    return {
        "first_segment": first,
        "last_segment": last,
        "evidence": evidence,
        "score": 80,
        "title": "Source moment",
        "reason": "The selected moment has an ending.",
        "category": "story",
        **changes,
    }


@pytest.mark.parametrize(
    "spoken",
    [
        ("Why did they bring a ladder?", "They wanted a higher ceiling for the budget."),
        ("What did the city change first?", "They replaced the narrow bridge with two lanes."),
    ],
)
def test_delayed_payoff_or_answer_can_extend_across_windows(spoken):
    segments = [
        Segment(0, 0, 5, spoken[0]),
        Segment(1, 7, 10, "Here is the detail that matters."),
        Segment(2, 30, 35, spoken[1]),
    ]
    opts = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        min_seconds=8,
        max_seconds=45,
        content_profile="interview",
    )
    windows = [segments[:2], segments[1:]]
    leads, invalid = ground_candidates(
        ProposedHighlights(candidates=[proposal(0, 1, spoken[0])]),
        windows[0],
        opts,
        40,
        "job",
    )
    assert invalid == 0
    chapters = build_context(windows)
    assert nearby_context(chapters, 0, 10)["nearest_chapters"][0]["question_segment"] == 0
    options, _ = shortlist(leads, windows, opts, chapters)
    decisions = EditorialProposal(candidates=[proposal(0, 2, spoken[1], option=0)])
    cuts, rejected = ground_review(decisions, options, opts, 40, "job", 1)
    assert rejected == Counter()
    assert [(cut.start, cut.end) for cut in cuts] == [(0, 35)]
    blocked = opts.model_copy(update={"max_seconds": 20})
    assert ground_review(decisions, options, blocked, 40, "job", 1)[1]["duration"] == 1


def test_note_grounding_and_semantic_beat_grouping_preserve_distinct_payoffs():
    passages = [
        Segment(0, 1, 12, "The library kept the original blueprints."),
        Segment(1, 60, 73, "The architectural plans stayed with the library."),
        Segment(2, 120, 131, "The blueprints revealed a hidden reading room."),
    ]
    opts = HighlightRequest(
        transcription_id=uuid4(), revision=1, remote_consent=True, min_seconds=8
    )
    notes = [
        NarrativeNote(
            first_segment=1,
            last_segment=1,
            role="callback",
            summary="The closing line revisits the plans",
            quote="architectural plans",
        ),
        NarrativeNote(
            first_segment=1,
            last_segment=1,
            role="payoff",
            summary="Unsupported",
            quote="missing source quote",
        ),
    ]
    grounded_notes = ground_narrative(notes, passages)
    assert [(note["segment_ids"], note["start"]) for note in grounded_notes] == [([1, 1], 60)]
    proposals = [proposal(s.id, s.id, s.text) for s in passages]
    leads, _ = ground_candidates(
        ProposedHighlights(candidates=proposals), passages, opts, 140, "job"
    )
    options, _ = shortlist(leads, [passages], opts, signals=grounded_notes)
    candidates = EditorialProposal(
        candidates=[
            proposal(0, 0, passages[0].text, option=0, beat_id=0),
            proposal(1, 1, passages[1].text, option=1, beat_id=0, score=90),
            proposal(2, 2, passages[2].text, option=2, beat_id=1),
        ]
    )
    cuts, rejected = ground_review(candidates, options, opts, 140, "job", 3)
    assert {cut.start for cut in cuts} == {60, 120}
    assert rejected["duplicate_beat"] == 1
    weak = EditorialProposal(
        candidates=[proposal(0, 0, passages[0].text, option=0, eligible=False)]
    )
    assert ground_review(weak, options, opts, 140, "job", 1) == ([], Counter(editorial_gate=1))


def test_media_sampling_handles_long_selection_exclusions_and_timestamped_audio():
    opts = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        analysis_start=62,
        analysis_end=21000,
        excluded_ranges=[{"start": 120, "end": 210}, {"start": 18500, "end": 19500}],
    )
    ranges = sample_ranges(opts, 21600, 12)
    assert len(ranges) == 12
    assert all(62 <= a < b <= 21000 and 15 <= b - a <= 60 for a, b in ranges)
    assert not any(overlaps(a, b, opts.excluded_ranges) for a, b in ranges)
    rms = "\n".join(
        f"[Parsed_ametadata_3 @ ptr] frame:{i} pts:1 pts_time:{i}\n"
        f"[Parsed_ametadata_3 @ ptr] lavfi.astats.Overall.RMS_level={db}"
        for i, db in enumerate(("-15", "-30", "-inf", "-30", "-30", "-10"))
    )
    assert acoustic_cues(audio_levels(rms, 1, 5)) == [
        {"kind": "quiet_audio", "start": 2.0, "end": 3.0, "rms_dbfs": -120.0}
    ]


def test_editorial_cut_preserves_pause_overlapping_timing_and_continuous_thought():
    opts = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        quality_mode=True,
        min_seconds=8,
        max_seconds=40,
    )
    transcript = [
        Segment(0, 1, 7, "First came the delayed setup."),
        Segment(1, 13, 22, "Then the first speaker started explaining"),
        Segment(2, 21, 29, "why the timing matters. The other voice responds.", continuation=True),
    ]
    quality = {
        "hook": 4,
        "context": 4,
        "payoff": 4,
        "standalone": 4,
        "opening_evidence": "First came",
        "ending_evidence": "voice responds.",
    }
    lead = proposal(0, 2, "First came the delayed setup.", quality=quality)
    candidates, _ = ground_candidates(
        ProposedHighlights(candidates=[lead]), transcript, opts, 40, "job"
    )
    clips, _ = shortlist(candidates, [transcript], opts)
    full, rejected = ground_review(
        EditorialProposal(candidates=[{**lead, "option": 0}]), clips, opts, 40, "job", 1
    )
    assert (full[0].start, full[0].end) == pytest.approx((0.88, 29.18))
    assert not rejected
    incomplete = EditorialProposal(candidates=[{**lead, "last_segment": 1, "option": 0}])
    assert ground_review(incomplete, clips, opts, 40, "job", 1)[1]["mid_thought_boundary"] == 1


def test_missing_media_degrades_to_speech_and_completed_discovery_is_reused(service):
    manager, recording, transcript, provider = service
    recorded_calls = []

    def fail_then_review(http_request):
        data = json.loads(http_request.content)
        recorded_calls.append(data)
        if len(recorded_calls) == 1:
            return httpx2.Response(503)
        return output({"candidates": []})

    manager.reviewer = EditorialReviewer(
        manager.settings, transport=httpx2.MockTransport(fail_then_review)
    )
    payload = request(transcript, multimodal=True)
    first = finished(manager, manager.submit(recording.id, payload).id)
    assert first.status == "failed" and first.media_status == "unavailable"
    retry = finished(manager, manager.submit(recording.id, payload).id)
    assert retry.discovery_cache_hit and retry.media_status == "unavailable"
    assert retry.status == "succeeded" and retry.candidates == []
    assert len(recorded_calls) == 2 and provider.calls == 1


def test_silent_video_frames_to_real_export(settings, tmp_path):
    configured = replace(settings, openai_api_key="synthetic-key", highlight_media_samples=1)
    path = tmp_path / "color-transition.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=red:size=160x90:duration=10:rate=24",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=160x90:duration=18:rate=24",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
        capture_output=True,
    )

    def vision(http_request):
        body = json.loads(http_request.content)
        assert body["model"] == configured.highlight_vision_model and not body["store"]
        images = [part for part in body["input"][0]["content"] if part["type"] == "input_image"]
        assert len(images) == 3  # A coarse visual change triggered one extra frame.
        assert all(part["image_url"].startswith("data:image/jpeg;base64,") for part in images)
        return output(
            {
                "observations": [
                    {
                        "sample_id": 0,
                        "description": "Plain red and blue screens.",
                        "title": "Color change",
                        "strong_moment": True,
                    },
                    {
                        "sample_id": 7,
                        "description": "Not collected.",
                        "title": "Unsupported",
                        "strong_moment": True,
                    },
                ]
            }
        )

    def editorial(http_request):
        body = json.loads(http_request.content)
        options = json.loads(body["input"].split("\nEDITORIAL OPTIONS DATA:\n")[1])
        assert len(options) == 1 and options[0]["segments"] == []
        lead = options[0]
        assert (lead["lead_start"], lead["lead_end"]) == (6, 22)
        frames = lead["sampled_frame_evidence"]["frame_times"]
        assert frames[0] == pytest.approx(8.88, abs=0.05)
        assert frames[-1] == pytest.approx(19.6, abs=0.05)
        anchor = lead["visual_anchor"]
        return output({"candidates": [proposal(anchor, anchor, lead["lead_evidence"], option=0)]})

    with TestClient(create_app(configured), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, path)
        manager = client.app.state.projects
        manager.highlights.sampler.transport = httpx2.MockTransport(vision)
        manager.highlights.reviewer.transport = httpx2.MockTransport(editorial)
        payload = {
            "id": str(uuid4()),
            "recording_id": recording["id"],
            "title": "Silent sample",
            "settings": {
                "analysis_start": 6,
                "analysis_end": 22,
                "multimodal": True,
                "headline_enabled": False,
            },
        }
        assert client.post("/api/projects", json=payload).status_code == 422
        response = client.post("/api/projects", json={**payload, "remote_consent": True})
        assert response.status_code == 202, response.text
        project = completed(client, payload["id"])
        assert len(project["items"]) == 1
        item = project["items"][0]
        job = manager.highlights.results.job(project["highlight_id"])
        assert (job.requests_made, job.input_tokens, job.output_tokens) == (2, 20, 10)
        assert job.media_status == "ready" and job.elapsed_seconds > 0
        assert job.vision_model == configured.highlight_vision_model
        assert (job.vision_input_tokens, job.vision_output_tokens) == (10, 5)
        assert item["selection_visual"]["description"] == "Plain red and blue screens."
        assert item["caption_preset"] == "none"
        assert (item["start"], item["end"]) == (6, 22)
        export = manager.store.export_path(item["export_id"])
        info = Media(configured).probe(export)
        assert not info.has_audio and info.duration == pytest.approx(16, abs=0.15)
        assert (info.width, info.height) == (1080, 1920)

        def center_pixel(at):
            return subprocess.run(
                [
                    settings.ffmpeg,
                    "-v",
                    "error",
                    "-ss",
                    str(at),
                    "-i",
                    str(export),
                    "-vf",
                    "crop=8:8,scale=1:1",
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
                timeout=15,
            ).stdout

        red = center_pixel(3.5)
        blue = center_pixel(4.5)
        assert red[0] > 220 and red[2] < 30
        assert blue[2] > 220 and blue[0] < 30


def test_local_audio_sampler_preserves_source_frame_times(settings, recordings):
    request = HighlightRequest(
        transcription_id=uuid4(),
        revision=1,
        remote_consent=True,
        min_seconds=8,
    )
    samples, cues, partial = MediaSampler(replace(settings, highlight_media_samples=1)).sample(
        recordings[True], request, 8, lambda: None, Event()
    )
    assert not partial and len(samples) == 1
    assert samples[0].frames[0].time == pytest.approx(1.44, abs=0.05)
    assert samples[0].frames[-1].time == pytest.approx(6.8, abs=0.05)
    assert cues == []  # A constant tone does not imply an interesting moment.
