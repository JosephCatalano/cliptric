"""API/persistence integration with explicit synthetic transcript fixtures.

Real ASR is verified separately; these inputs isolate caption/edit/export contracts.
"""

import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.captions import CaptionCustomization
from backend.main import create_app
from backend.models import ExportRequest
from backend.storage import Store
from backend.tests.test_pipeline import await_export, import_video
from backend.tests.test_pipeline import client as client
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


def transcript(client, recording, tmp_path):
    service = client.app.state.transcriptions
    job = service.transcripts.create(str(uuid4()), recording["id"])
    result = tmp_path / f"{job.id}.ndjson"
    words = [
        {"id": 0, "start": 1.0, "end": 1.5, "text": "First", "probability": 0.9},
        {"id": 1, "start": 1.6, "end": 2.1, "text": "caption.", "probability": 0.8},
        {"id": 2, "start": 3.0, "end": 3.5, "text": "Second.", "probability": 0.9},
    ]
    result.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
    service.transcripts.publish(
        job.id, "synthetic-source", "synthetic-config", result, recording["duration"], len(words)
    )
    return job.id


def test_draft_and_corrections_survive_restart(settings, recordings, tmp_path):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as api:
        recording = import_video(api, recordings[True])
        draft_url = f"/api/recordings/{recording['id']}/draft"
        draft = api.get(draft_url).json()
        assert draft["revision"] == 0
        draft.update(start=1, end=4, mode="crop", captions_enabled=True, caption_position="upper")
        draft["caption_customization"] = {
            "grouping": "two",
            "capitalization": "upper",
            "font_size": 80,
        }
        saved = api.put(draft_url, json=draft)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert api.put(draft_url, json=draft).status_code == 409
        identifier = transcript(api, recording, tmp_path)
        patch = {
            "revision": 1,
            "updates": [{"id": 0, "text": "Corrected", "start": 0.9, "end": 1.45}],
        }
        assert (
            api.patch(f"/api/transcriptions/{identifier}/words", json=patch).json()["revision"] == 2
        )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as reopened:
        assert reopened.get(draft_url).json() == saved.json()
        words = reopened.get(f"/api/transcriptions/{identifier}/words?start=1&end=4").json()
        assert words["revision"] == 2
        assert words["words"][0]["text"] == "Corrected"
        assert words["words"][0]["edited"] is True
        assert words["words"][0]["start"] == 0.9
        assert words["words"][0]["end"] == 1.45
        assert words["words"][0]["original_start"] == 1
        assert words["words"][0]["timing_edited"] is True


def test_captioned_export_snapshots_saved_revision(client, settings, recordings, tmp_path):
    recording = import_video(client, recordings[True])
    identifier = transcript(client, recording, tmp_path)
    url = f"/api/recordings/{recording['id']}/clips"
    options = {"transcription_id": identifier, "revision": 1, "position": "lower"}
    submitted = client.post(url, json={"start": 1, "end": 4, "captions": options})
    assert submitted.status_code == 202, submitted.text
    clip_id = submitted.json()["id"]
    # Editing during rendering must not alter the already accepted export.
    edited = client.patch(
        f"/api/transcriptions/{identifier}/words",
        json={"revision": 1, "updates": [{"id": 0, "text": "Changed"}]},
    )
    assert edited.status_code == 200
    complete = await_export(client, clip_id)
    assert complete["status"] == "succeeded", complete
    expected_options = {
        **options,
        "preset": "classic",
        "emphasis_words": [],
        "customization": CaptionCustomization().model_dump(),
    }
    assert complete["captions"] == expected_options
    assert Store(settings.data_dir).caption_words(clip_id)[0]["text"] == "First"
    assert client.get(f"/api/clips/{clip_id}/media").status_code == 200
    stale = client.post(url, json={"start": 1, "end": 4, "captions": options})
    assert stale.status_code == 409
    assert client.get(url).json()[0]["captions"] == expected_options


def test_caption_source_mismatch_empty_selection_and_invalid_position(client, recordings, tmp_path):
    recording = import_video(client, recordings[True])
    other = import_video(client, recordings[False])
    identifier = transcript(client, recording, tmp_path)
    options = {"transcription_id": identifier, "revision": 1, "position": "centre"}
    mismatch = client.post(
        f"/api/recordings/{other['id']}/clips", json={"start": 1, "end": 4, "captions": options}
    )
    assert mismatch.status_code == 404
    url = f"/api/recordings/{recording['id']}/clips"
    assert client.post(url, json={"start": 5, "end": 7, "captions": options}).status_code == 422
    assert (
        client.post(
            url, json={"start": 1, "end": 4, "captions": {**options, "position": "arbitrary"}}
        ).status_code
        == 422
    )
    assert client.get(url).json() == []


def test_additive_migration_and_caption_cleanup_preserve_existing_media(settings, recordings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as api:
        recording = import_video(api, recordings[False])
    store = Store(settings.data_dir)
    clip = store.create_clip(str(uuid4()), recording["id"], ExportRequest(start=0, end=1))
    store.update_clip(clip.id, "succeeded", duration=1, size_bytes=10)
    output = store.export_path(clip.id)
    output.write_bytes(b"saved-data")
    with sqlite3.connect(store.database) as db:
        db.executescript(
            "DROP TABLE clip_captions; DROP TABLE selection_drafts; PRAGMA user_version=1;"
        )
    partial = settings.data_dir / "exports" / f"{uuid4()}.partial.ass"
    partial.write_text("interrupted caption artifact", encoding="utf-8")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as api:
        assert api.get(f"/api/clips/{clip.id}").json()["status"] == "succeeded"
        assert api.get(f"/api/recordings/{recording['id']}/draft").status_code == 200
    assert output.read_bytes() == b"saved-data"
    assert store.source_path(recording["id"]).read_bytes() == recordings[False].read_bytes()
    assert not partial.exists()


@pytest.mark.parametrize(
    "values", [{"start": 3, "end": 2}, {"end": 9}, {"revision": -1}, {"caption_position": "bottom"}]
)
def test_invalid_drafts_do_not_replace_saved_selection(client, recordings, values):
    recording = import_video(client, recordings[True])
    url = f"/api/recordings/{recording['id']}/draft"
    original = client.get(url).json()
    assert client.put(url, json={**original, **values}).status_code == 422
    assert client.get(url).json() == original
