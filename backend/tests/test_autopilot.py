"""Autopilot through the real ASGI routes, Twitch worker, and local renderer."""

import shutil
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_twitch_discovery import ROOT, clip, poll, run_scan, scan_config, setup_api
from backend.tests.test_twitch_discovery import settings as settings
from backend.tests.test_twitch_discovery import tools as tools
from backend.twitch_rights import publishing_error

URL = "/api/autopilot"


def change(client, scan, **options):
    current = client.get(URL).json()["configuration"]
    return client.put(URL, json={**current, "scan_ids": [scan["id"]], "enabled": True, **options})


def rights(client, service):
    with service.store.connection() as db:
        db.execute(
            "INSERT INTO twitch_editor VALUES(1,'42','fixture',?,?)",
            (b"test credential", time.time()),
        )
    response = client.post(
        f"{ROOT}/rights",
        json={
            "broadcaster_id": "42",
            "rights": True,
            "allow_publish": True,
            "allow_external_ai": True,
            "expires": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 204, response.text


def test_discover_pauses_automatic_scans_preserves_manual_runs_and_survives_restart(
    settings, monkeypatch
):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, lambda _: ([clip()], None, 0))
        saved = client.post(
            f"{ROOT}/scans", json=scan_config(enabled=False, auto_prepare=True)
        ).json()
        assert change(client, saved).status_code == 409
        response = change(client, saved, mode="discover")
        assert response.status_code == 200, response.text
        assert client.get(URL).json()["scans"][0]["enabled"]
        path = f"{ROOT}/scans/{saved['id']}"
        poll(client, path, lambda doc: doc["runs"] and doc["runs"][0]["status"] == "complete")
        assert not client.get(URL).json()["jobs"]
        assert change(client, saved, enabled=False, mode="discover").status_code == 200
        with service.store.connection() as db:
            db.execute("UPDATE twitch_scans SET next_run=?", (time.time() - 1,))
        assert service.claim() is None
        manual = run_scan(client, saved)
        assert manual["clips"] and not manual["intake"]
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        assert client.get(URL).json()["configuration"]["enabled"] is False


def test_pause_during_discovery_stops_intake_and_scan_revision_change_blocks_resumption(
    settings, monkeypatch
):
    app = create_app(settings)
    begun, release = threading.Event(), threading.Event()

    def page(_):
        begun.set()
        assert release.wait(10)
        return [clip()], None, 0

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, page)
        rights(client, service)
        saved = client.post(f"{ROOT}/scans", json=scan_config(enabled=True)).json()
        response = change(client, saved)
        assert response.status_code == 200, response.text
        assert begun.wait(5)
        try:
            assert change(client, saved, enabled=False).status_code == 200
        finally:
            release.set()
        path = f"{ROOT}/scans/{saved['id']}"
        poll(client, path, lambda doc: doc["runs"] and doc["runs"][0]["status"] == "complete")
        assert not client.get(URL).json()["jobs"]
        response = client.put(
            f"{path}?revision=1", json=scan_config(enabled=True, name="Edited scan")
        )
        assert response.status_code == 200, response.text
        assert client.get(URL).json()["scans"][0]["changed"]


def test_autopilot_renders_and_holds_silent_clip_when_captions_are_required(
    settings, monkeypatch, tmp_path
):
    source = tmp_path / "synthetic.mp4"
    subprocess.run(
        [
            settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=1",
            "-an",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    app = create_app(replace(settings, render_encoder="cpu", openai_api_key="fixture-not-live"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, lambda _: ([clip()], None, 0))
        rights(client, service)
        intake = service.intake
        monkeypatch.setattr(
            intake.editor, "downloads", lambda *_: {"test-clip": "https://example.test/media"}
        )

        def download(_url, dest, _limit, _stop):
            shutil.copyfile(source, dest)
            return source.stat().st_size

        monkeypatch.setattr(intake, "download", download)
        saved = client.post(f"{ROOT}/scans", json=scan_config(enabled=True)).json()
        assert change(client, saved, ai_review=True).status_code == 422
        response = change(client, saved, require_captions=True, ai_review=True, ai_consent=True)
        assert response.status_code == 200, response.text
        snapshot = poll(
            client,
            URL,
            lambda doc: (
                bool(doc["jobs"]) and doc["jobs"][0]["status"] in {"ready", "partial", "failed"}
            ),
        )
        assert snapshot["jobs"][0]["status"] == "ready", snapshot
        draft = snapshot["jobs"][0]["clips"][0]
        assert draft["checks"] and "requires captions" in draft["quality"] and not draft["ready"]
        assert "requires captions" in publishing_error(
            service.store,
            draft["export_id"],
            "youtube",
            "a" * 64,
            uuid4(),
        )
        media = intake.media.probe(service.store.export_path(draft["export_id"]))
        assert (media.width, media.height, media.has_audio) == (1080, 1920, False)


def test_autopilot_review_uses_existing_editorial_pipeline(
    settings, recordings, monkeypatch, tmp_path
):
    import json

    from backend.batch_review import ReviewProposal

    calls = []
    app = create_app(replace(settings, render_encoder="cpu", openai_api_key="fixture-not-live"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        service = setup_api(app, monkeypatch, lambda _: ([clip()], None, 0))
        rights(client, service)
        intake = service.intake
        monkeypatch.setattr(
            intake.editor, "downloads", lambda *_: {"test-clip": "https://example.test/media"}
        )

        def download(_url, dest, _limit, _stop):
            shutil.copyfile(recordings[True], dest)
            return dest.stat().st_size

        transcripts = app.state.transcriptions.transcripts

        def transcribe(recording_id, *, identifier, **_):
            job = transcripts.create(identifier, recording_id)
            path = tmp_path / "autopilot-words.ndjson"
            words = [
                {
                    "id": index,
                    "start": 1 + index * 0.5,
                    "end": 1.4 + index * 0.5,
                    "text": value,
                    "probability": 0.99,
                }
                for index, value in enumerate(["The", "car", "escaped."])
            ]
            path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
            transcripts.publish(job.id, "fixture", "fixture", path, 8, len(words))
            return transcripts.job(job.id)

        async def review(segments, duration, **_):
            calls.append(duration)
            return (
                ReviewProposal(
                    decision="keep_full",
                    reason="The complete clip preserves the payoff.",
                    candidate={
                        "first_segment": segments[0].id,
                        "last_segment": segments[-1].id,
                        "title": "The car escaped",
                        "evidence": "The car escaped.",
                        "reason": "The transcript supports the outcome.",
                        "score": 80,
                        "category": "story",
                    },
                ),
                100,
                30,
            )

        monkeypatch.setattr(intake, "download", download)
        monkeypatch.setattr(app.state.transcriptions, "submit", transcribe)
        monkeypatch.setattr(app.state.projects.batch_review, "suggest", review)
        saved = client.post(f"{ROOT}/scans", json=scan_config(enabled=True)).json()
        response = change(client, saved, require_captions=True, ai_review=True, ai_consent=True)
        assert response.status_code == 200, response.text
        snapshot = poll(
            client,
            URL,
            lambda doc: (
                bool(doc["jobs"]) and doc["jobs"][0]["status"] in {"ready", "partial", "failed"}
            ),
        )
        assert snapshot["jobs"][0]["status"] == "ready", snapshot
        draft = snapshot["jobs"][0]["clips"][0]
        assert draft["checks"] and draft["ready"] and not draft["quality"]
        assert draft["title"] == "The car escaped" and draft["reason"]
        assert len(calls) == 1 and not snapshot["post_handoffs"]
        export = intake.media.probe(service.store.export_path(draft["export_id"]))
        assert export.has_audio and (export.width, export.height) == (1080, 1920)
