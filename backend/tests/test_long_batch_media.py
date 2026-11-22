"""Real multi-minute FFmpeg exports; transcript and editorial decision are fixtures."""

import json
import subprocess
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.jobs as jobs
from backend.batch_review import ReviewProposal
from backend.main import create_app
from backend.media import Media
from backend.tests.test_batches import edit
from backend.tests.test_pipeline import import_video
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_project_media import completed


def test_real_three_minute_captioned_whole_video_and_late_trim(settings, tmp_path, monkeypatch):
    source = tmp_path / "three-minute-source.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:size=320x180:rate=1:duration=181",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=181",
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
    subtitles = []
    original = jobs.write_ass

    def capture(*args, **kwargs):
        count = original(*args, **kwargs)
        subtitles.append(args[3].read_text(encoding="utf-8-sig"))
        return count

    monkeypatch.setattr(jobs, "write_ass", capture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, source)
        manager = client.app.state.projects
        transcript = manager.transcriptions.transcripts.create(str(uuid4()), recording["id"])
        words = [
            {
                "id": index,
                "start": float(start),
                "end": float(start + 5),
                "text": text,
                "probability": 0.9,
            }
            for index, (start, text) in enumerate(
                (
                    (1, "A complete opening."),
                    (90, "A developed middle."),
                    (170, "A resolved ending."),
                )
            )
        ]
        path = tmp_path / "long-words.ndjson"
        path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
        manager.transcriptions.transcripts.publish(
            transcript.id, "synthetic", "synthetic", path, recording["duration"], len(words)
        )
        calls = []

        async def review(segments, duration, **kwargs):
            calls.append(duration)
            return (
                ReviewProposal(
                    decision="keep_full",
                    reason="Synthetic complete story judgment.",
                    candidate={
                        "first_segment": 0,
                        "last_segment": 2,
                        "title": "A complete story",
                        "score": 80,
                        "reason": "Synthetic full source",
                        "evidence": "A complete opening.",
                        "category": "story",
                        "quality": None,
                    },
                ),
                50,
                20,
            )

        monkeypatch.setattr(manager.batch_review, "suggest", review)
        response = client.post(
            "/api/projects",
            json={
                "id": str(uuid4()),
                "title": "Long batch",
                "recording_ids": [recording["id"]],
                "remote_consent": True,
                "settings": {
                    "duration_mode": "full",
                    "batch_mode": "auto",
                    "analysis_end": recording["duration"],
                    "caption_preset": "flow",
                    "caption_position": "auto",
                },
            },
        )
        assert response.status_code == 202, response.text
        result = completed(client, response.json()["id"])
        item = result["items"][0]
        info = Media(settings).probe(manager.store.export_path(item["export_id"]))
        assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
        assert abs(info.duration - recording["duration"]) <= 0.1 and info.duration > 180
        assert "0:02:50.00" in subtitles[0] and "resolved ending" in subtitles[0]
        assert item["analysis_choice"] == "keep_full" and len(calls) == 1
        response = client.patch(
            f"/api/projects/{result['id']}/clips/{item['id']}",
            json=edit(item, start=130.0, end=175.0),
        )
        assert response.status_code == 202, response.text
        changed = completed(client, result["id"])
        trimmed = changed["items"][0]
        assert abs(trimmed["export"]["duration"] - 45) <= 0.1
        assert "0:00:40.00" in subtitles[1] and "resolved ending" in subtitles[1]
        assert manager.store.export_path(item["export_id"]).exists()
        assert manager.store.source_path(recording["id"]).read_bytes() == source.read_bytes()
        assert len(calls) == 1
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as reopened:
        saved = reopened.get(f"/api/projects/{result['id']}").json()
        assert saved["items"][0]["export_id"] == trimmed["export_id"]
        assert saved["items"][0]["analysis_choice"] == "keep_full"
