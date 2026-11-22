"""Real API/persistence/export path with explicitly simulated OpenAI transport."""

import json
from dataclasses import replace
from uuid import uuid4

import httpx2
from fastapi.testclient import TestClient

import backend.main as main
from backend.editorial_review import EditorialReviewer
from backend.highlight_provider import OpenAIHighlights
from backend.tests.test_highlights import finished
from backend.tests.test_pipeline import await_export, import_video
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools


def test_suggestion_review_saved_selection_and_captioned_export(
    settings, recordings, tmp_path, monkeypatch
):
    calls = []

    def handler(request):
        calls.append(request)
        result = {
            "candidates": [
                {
                    "first_segment": 0,
                    "last_segment": 3,
                    "score": 80,
                    "title": "A synthetic lesson",
                    "reason": "A complete synthetic test passage.",
                    "evidence": "First lesson.",
                    "category": "insight",
                }
            ]
        }
        if json.loads(request.content)["text"]["format"]["name"] == "editorial_review":
            result["candidates"][0]["option"] = 0
        return httpx2.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(result)}],
                    }
                ],
                "usage": {"input_tokens": 200, "output_tokens": 100},
            },
        )

    monkeypatch.setattr(
        main,
        "OpenAIHighlights",
        lambda config: OpenAIHighlights(config, transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(
        main,
        "EditorialReviewer",
        lambda config: EditorialReviewer(config, transport=httpx2.MockTransport(handler)),
    )
    configured = replace(settings, openai_api_key="synthetic-key")
    with TestClient(main.create_app(configured), base_url="http://127.0.0.1:8765") as client:
        recording = import_video(client, recordings[True])
        manager = client.app.state.highlights
        transcript = manager.transcripts.create(str(uuid4()), recording["id"])
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
        source = tmp_path / "synthetic-transcript.ndjson"
        source.write_text("".join(json.dumps(w) + "\n" for w in words), encoding="utf-8")
        manager.transcripts.publish(
            transcript.id,
            "synthetic-source",
            "synthetic-config",
            source,
            recording["duration"],
            len(words),
        )
        url = f"/api/recordings/{recording['id']}/highlights"
        payload = {
            "transcription_id": transcript.id,
            "revision": 1,
            "remote_consent": True,
            "min_seconds": 8,
            "max_seconds": 8,
        }
        assert client.post(url, json={**payload, "remote_consent": False}).status_code == 422
        submitted = client.post(url, json=payload)
        assert submitted.status_code == 202, submitted.text
        job = finished(manager, submitted.json()["id"])
        assert job.status == "succeeded", job.error
        candidate = job.candidates[0]
        assert (candidate.start, candidate.end) == (0, 8)
        review = client.patch(
            f"/api/highlight-candidates/{candidate.id}", json={"review": "accepted", "revision": 0}
        )
        assert review.status_code == 200
        draft_url = f"/api/recordings/{recording['id']}/draft"
        draft = client.get(draft_url).json()
        draft.update(start=candidate.start, end=candidate.end, captions_enabled=True)
        saved = client.put(draft_url, json=draft)
        assert saved.status_code == 200
        exported = client.post(
            f"/api/recordings/{recording['id']}/clips",
            json={
                "start": candidate.start,
                "end": candidate.end,
                "captions": {"transcription_id": transcript.id, "revision": 1, "position": "lower"},
            },
        )
        assert exported.status_code == 202
        completed = await_export(client, exported.json()["id"])
        assert completed["status"] == "succeeded", completed
        assert client.get(f"/api/clips/{completed['id']}/media").status_code == 200
        assert client.post(url, json=payload).json()["id"] == job.id
        assert len(calls) == 2
    with TestClient(main.create_app(configured), base_url="http://127.0.0.1:8765") as reopened:
        state = reopened.get(url).json()
        assert state["result"]["id"] == job.id
        assert state["result"]["candidates"][0]["review"] == "accepted"
        assert reopened.get(draft_url).json() == saved.json()
        assert reopened.get(f"/api/clips/{completed['id']}/media").status_code == 200
