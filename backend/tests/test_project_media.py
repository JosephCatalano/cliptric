"""Actual FFmpeg renders and persisted project delivery; AI output is simulated."""

import io
import json
import subprocess
import time
import zipfile
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.jobs as jobs
import backend.main as main
from backend.highlight_models import ProposedHighlights, ProviderResult
from backend.tests.editorial_fixtures import SyntheticEditorialReviewer
from backend.tests.test_pipeline import import_video
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


def completed(client, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        project = client.get(f"/api/projects/{identifier}").json()
        if project["status"] not in {
            "queued",
            "transcribing",
            "analysing",
            "rendering",
            "cancelling",
        }:
            assert project["status"] == "ready", project
            return project
        time.sleep(0.05)
    raise AssertionError("Real media project timed out")


def test_actual_caption_presets_thumbnails_trim_zip_and_reopen(
    settings, recordings, tmp_path, monkeypatch
):
    calls, subtitles = [], []

    class Provider:
        async def suggest(self, window, request):
            calls.append(request)
            return ProviderResult(
                ProposedHighlights(
                    candidates=[
                        dict(
                            first_segment=0,
                            last_segment=3,
                            title="Synthetic speech moment",
                            score=80,
                            reason="Synthetic test passage",
                            evidence="First lesson.",
                            category="insight",
                        )
                    ]
                ),
                100,
                30,
            )

    original = jobs.write_ass

    def capture(*args, **kwargs):
        count = original(*args, **kwargs)
        subtitles.append((kwargs["preset"], args[3].read_text(encoding="utf-8-sig")))
        return count

    monkeypatch.setattr(jobs, "write_ass", capture)
    monkeypatch.setattr(main, "OpenAIHighlights", lambda config: Provider())
    monkeypatch.setattr(main, "EditorialReviewer", SyntheticEditorialReviewer)
    configured = replace(settings, openai_api_key="synthetic-key")
    with TestClient(main.create_app(configured), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, recordings[True])
        manager = client.app.state.projects
        transcript = manager.transcriptions.transcripts.create(str(uuid4()), recording["id"])
        words = [
            {
                "id": i,
                "start": float(i * 2),
                "end": float(i * 2 + 2),
                "text": text,
                "probability": 0.9,
            }
            for i, text in enumerate(
                ["First lesson.", "Second lesson.", "Third lesson.", "Final lesson."]
            )
        ]
        path = tmp_path / "synthetic-words.ndjson"
        path.write_text("".join(json.dumps(w) + "\n" for w in words), encoding="utf-8")
        manager.transcriptions.transcripts.publish(
            transcript.id, "test-source", "test-config", path, recording["duration"], 4
        )
        response = client.post(
            "/api/projects",
            json={
                "id": str(uuid4()),
                "recording_id": recording["id"],
                "title": "Real media test",
                "settings": {
                    "analysis_end": recording["duration"],
                    "caption_preset": "bold",
                    "max_clips": 1,
                },
            },
        )
        assert response.status_code == 202, response.text
        project = completed(client, response.json()["id"])
        item = project["items"][0]
        assert item["export"]["headline"]["text"] == "Synthetic editorial moment 0"
        original_id = item["export_id"]
        assert project["thumbnail"] and item["thumbnail"]
        preview = client.get(f"/api/projects/{project['id']}/thumbnail?item_id={item['id']}")
        assert preview.status_code == 200 and preview.content.startswith(b"\xff\xd8")
        assert client.get(f"/api/clips/{original_id}/media?download=true").status_code == 200
        edit = {
            k: item[k]
            for k in ("title", "start", "end", "caption_preset", "caption_position", "revision")
        }
        edit.update(
            start=1.0,
            end=7.0,
            caption_preset="boxed",
            framing="split",
            split_layout={
                "webcam": {"x": 0.75, "y": 0.0, "width": 0.25, "height": 0.25},
                "gameplay": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            },
            quality={"cover_at": 2.0},
        )
        assert (
            client.patch(f"/api/projects/{project['id']}/clips/{item['id']}", json=edit).status_code
            == 202
        )
        changed = completed(client, project["id"])
        changed_item = changed["items"][0]
        clip = changed_item["export"]
        assert abs(clip["duration"] - 6) <= 0.1 and clip["captions"]["preset"] == "boxed"
        assert clip["headline"]["seconds"] == 4
        assert changed_item["thumbnail_export_id"] == clip["id"] != original_id
        assert clip["performance"]["split_cover"]
        source = manager.store.export_path(clip["id"])
        cover_thumbnail = tmp_path / "cover-thumbnail.jpg"
        manager.media.thumbnail(source, cover_thumbnail, 0, manager.stop)
        clip_thumbnail_url = f"/api/projects/{project['id']}/thumbnail?item_id={item['id']}"
        thumbnail = client.get(clip_thumbnail_url)
        assert thumbnail.status_code == 200
        assert thumbnail.content == cover_thumbnail.read_bytes()
        assert thumbnail.content != preview.content

        def pixel(path, time_at, x, y):
            return subprocess.run(
                [
                    settings.ffmpeg,
                    "-v",
                    "error",
                    "-ss",
                    str(time_at),
                    "-i",
                    str(path),
                    "-vf",
                    f"format=rgb24,crop=1:1:{x}:{y}",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    "-f",
                    "rawvideo",
                    "-",
                ],
                check=True,
                capture_output=True,
                timeout=10,
            ).stdout

        # The centre of the opening frame's top panel shows the saved webcam crop.
        webcam = pixel(recordings[True], 2, 280, 22)
        top_panel = pixel(source, 0, 540, 320)
        assert len(webcam) == len(top_panel) == 3
        assert sum(abs(a - b) for a, b in zip(webcam, top_panel, strict=True)) < 50

        # An explicit update replaces a legacy split cover without modifying its MP4.
        with manager.store.connection() as db:
            db.execute(
                "UPDATE clip_performance SET payload=json_remove(payload,'$.split_cover') WHERE clip_id=?",
                (clip["id"],),
            )
        assert client.post(f"/api/projects/{project['id']}/resume", json={}).status_code == 202
        refreshed = completed(client, project["id"])["items"][0]
        assert refreshed["export_id"] != clip["id"]
        assert source.is_file()
        assert refreshed["thumbnail_export_id"] == refreshed["export_id"]
        clip = refreshed["export"]
        assert clip["performance"]["split_cover"]
        assert [preset for preset, _ in subtitles] == ["bold", "boxed", "boxed"]
        assert "&H0000EFFF" in subtitles[0][1] and ",3,10,0," in subtitles[1][1]
        bundle = client.get(f"/api/projects/{project['id']}/bundle")
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["included"][0]["export_id"] == clip["id"]
            assert (
                archive.read(manifest["included"][0]["file"])
                == manager.store.export_path(clip["id"]).read_bytes()
            )
        assert len(calls) == 1
    with TestClient(main.create_app(configured), base_url="http://127.0.0.1:8765") as reopened:
        saved = reopened.get(f"/api/projects/{project['id']}").json()
        assert saved["settings"] == project["settings"]
        assert saved["items"][0]["export_id"] == clip["id"]
        assert reopened.get(f"/api/clips/{original_id}/media").status_code == 200
        assert reopened.get(f"/api/clips/{clip['id']}/media").status_code == 200
