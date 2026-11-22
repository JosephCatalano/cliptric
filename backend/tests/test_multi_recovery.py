"""Larger supplied batches and cancellation use explicit synthetic providers/media."""

import asyncio
import io
import json
import threading
import zipfile

from backend.batch_review import MultiReviewProposal
from backend.tests.test_batch_review import proposal
from backend.tests.test_batches import batch, create
from backend.tests.test_multi_presentation import choices, request
from backend.tests.test_multi_presentation import context as context
from backend.tests.test_multi_presentation import settings as settings
from backend.tests.test_multi_presentation import tools as tools
from backend.tests.test_projects import finished, seed


def test_more_than_twenty_results_support_selected_zip_and_shared_styles(
    context, monkeypatch, tmp_path
):
    client, manager, recording, _, _ = context
    sources = [recording] + [seed(manager, tmp_path) for _ in range(4)]
    candidates = []
    for first, last in ((0, 2), (3, 5), (6, 8), (9, 11), (12, 15)):
        candidate = proposal("trim").candidate.model_copy(deep=True)
        candidate.first_segment, candidate.last_segment = first, last
        candidate.evidence = f"Evidence {first}."
        candidate.quality.opening_evidence = f"Evidence {first}."
        candidate.quality.ending_evidence = f"Evidence {last}."
        candidates.append(candidate)
    calls = []

    async def review(*args, **kwargs):
        calls.append(True)
        return (
            MultiReviewProposal(
                decision="clips", candidates=candidates, reason="Five distinct moments."
            ),
            100,
            30,
        )

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    result = create(
        client,
        {
            **batch(sources, batch_mode="highlights", clips_per_source=5, quality_mode=True),
            "remote_consent": True,
        },
    )
    assert result["status"] == "ready" and len(result["items"]) == 25, result
    assert len(calls) == 5 and sum(i["analysis_input_tokens"] for i in result["items"]) == 500
    ids = ",".join(i["id"] for i in result["items"])
    response = client.get(f"/api/projects/{result['id']}/bundle?ids={ids}")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len(json.loads(archive.read("manifest.json"))["included"]) == 25
    response = client.post(
        f"/api/projects/{result['id']}/caption-style",
        json={
            "style": {"preset": "flow", "position": "lower"},
            "targets": [{"id": i["id"], "revision": i["revision"]} for i in result["items"]],
        },
    )
    assert response.status_code == 202, response.text
    updated = finished(client, result["id"])
    assert updated["status"] == "ready" and len(calls) == 5
    assert all(i["export"]["captions"]["preset"] == "flow" for i in updated["items"])


def test_cancelled_multi_review_saves_all_decisions_and_resume_does_not_rebill(
    context, monkeypatch
):
    client, manager, recording, _, _ = context
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def review(*args, **kwargs):
        calls.append(True)
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return choices(), 10, 5

    monkeypatch.setattr(manager.batch_highlights, "suggest", review)
    response = client.post("/api/projects", json=request(recording))
    identifier = response.json()["id"]
    assert entered.wait(3)
    try:
        assert client.post(f"/api/projects/{identifier}/cancel").status_code == 200
    finally:
        release.set()
    value = finished(client, identifier)
    assert value["status"] == "cancelled" and len(value["items"]) == 2
    assert all(i["analysis_complete"] for i in value["items"])
    client.post(f"/api/projects/{identifier}/resume", json={})
    assert finished(client, identifier)["status"] == "ready" and len(calls) == 1


def test_highlight_mode_validates_explicit_consent_and_per_source_budget(context):
    client, _, recording, _, _ = context
    value = request(recording)
    assert client.post("/api/projects", json={**value, "remote_consent": False}).status_code == 422
    for invalid in (0, 6, True):
        assert (
            client.post(
                "/api/projects",
                json={**value, "settings": {**value["settings"], "clips_per_source": invalid}},
            ).status_code
            == 422
        )
