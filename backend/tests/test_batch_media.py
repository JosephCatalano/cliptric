"""Real FFmpeg supplied-batch exports; speech timings are explicit synthetic fixtures."""

import io
import json
import zipfile
from uuid import uuid4

from fastapi.testclient import TestClient

import backend.jobs as jobs
from backend.main import create_app
from backend.media import Media
from backend.tests.test_pipeline import import_video
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tests.test_project_media import completed


def test_real_supplied_sources_caption_snapshots_zip_and_reopen(
    settings, recordings, tmp_path, monkeypatch
):
    subtitles = []
    original = jobs.write_ass

    def capture(*args, **kwargs):
        count = original(*args, **kwargs)
        subtitles.append(args[3].read_text(encoding="utf-8-sig"))
        return count

    monkeypatch.setattr(jobs, "write_ass", capture)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        sources = []
        manager = client.app.state.projects
        for index, text in enumerate(("First supplied clip.", "Second supplied clip.")):
            recording = import_video(client, recordings[True], f"Campaign {index + 1}.mp4")
            sources.append(recording)
            transcript = manager.transcriptions.transcripts.create(str(uuid4()), recording["id"])
            path = tmp_path / f"batch-words-{index}.ndjson"
            path.write_text(
                json.dumps({"id": 0, "start": 0.5, "end": 2.0, "text": text, "probability": 0.9})
                + "\n",
                encoding="utf-8",
            )
            manager.transcriptions.transcripts.publish(
                transcript.id,
                "synthetic-source",
                "synthetic-config",
                path,
                recording["duration"],
                1,
            )
        response = client.post(
            "/api/projects",
            json={
                "id": str(uuid4()),
                "title": "Real media batch",
                "recording_ids": [source["id"] for source in sources],
                "settings": {
                    "duration_mode": "full",
                    "analysis_end": max(source["duration"] for source in sources),
                    "caption_preset": "flow",
                    "caption_position": "auto",
                },
            },
        )
        assert response.status_code == 202, response.text
        project = completed(client, response.json()["id"])
        exports = []
        for source, item in zip(sources, project["items"], strict=True):
            clip = item["export"]
            assert (
                clip["recording_id"] == source["id"]
                and clip["captions"]["transcription_id"] == item["transcription_id"]
            )
            assert clip["start"] == 0 and clip["end"] == source["duration"]
            output = manager.store.export_path(clip["id"])
            info = Media(settings).probe(output)
            assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
            assert abs(info.duration - source["duration"]) <= 0.1
            assert clip["headline"]["text"] == source["name"].rsplit(".", 1)[0]
            assert (
                manager.store.source_path(source["id"]).read_bytes()
                == recordings[True].read_bytes()
            )
            exports.append(clip["id"])
        assert len(subtitles) == 2
        assert (
            "First supplied clip." in subtitles[0] and "Second supplied clip." not in subtitles[0]
        )
        assert (
            "Second supplied clip." in subtitles[1] and "First supplied clip." not in subtitles[1]
        )
        bundle = client.get(f"/api/projects/{project['id']}/bundle")
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert len(manifest["included"]) == 2 and not manifest["excluded"]
            for entry in manifest["included"]:
                assert (
                    archive.read(entry["file"])
                    == manager.store.export_path(entry["export_id"]).read_bytes()
                )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as reopened:
        result = reopened.get(f"/api/projects/{project['id']}").json()
        assert [item["export_id"] for item in result["items"]] == exports
        assert result["recording_ids"] == [source["id"] for source in sources]
