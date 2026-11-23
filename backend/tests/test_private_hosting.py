"""Exercise the real API boundary and OAuth routing without provider calls."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.instagram_api import InstagramSettings
from backend.main import create_app
from backend.social_transport import SocialSettings
from backend.tests.test_pipeline import recordings as recordings
from backend.tests.test_pipeline import settings as settings
from backend.tests.test_pipeline import tools as tools
from backend.tiktok_api import TikTokSettings

ORIGIN = "https://cliptric.com"
GATEWAY = {"X-Cliptric-Gateway": "a" * 64}


@pytest.fixture
def private_app(settings, monkeypatch):
    monkeypatch.setattr(
        InstagramSettings, "load", lambda: InstagramSettings("1", "fixture-secret", "2", "3")
    )
    monkeypatch.setattr(TikTokSettings, "load", lambda: TikTokSettings("key", "fixture-secret"))
    monkeypatch.setattr(
        SocialSettings,
        "load",
        lambda platform: SocialSettings(
            "4", "fixture-secret", "5" if platform == "facebook" else ""
        ),
    )
    return create_app(
        replace(settings, public_origin=ORIGIN, gateway_token=GATEWAY["X-Cliptric-Gateway"])
    )


@pytest.mark.parametrize(
    "origin", ["http://cliptric.com", "https://cliptric.com/path", "https://cliptric.com:443"]
)
def test_public_origin_requires_bare_https(settings, origin):
    with pytest.raises(ValueError, match="bare HTTPS domain"):
        replace(settings, public_origin=origin, gateway_token="a" * 64)


def test_public_origin_requires_configured_gateway(settings):
    with pytest.raises(ValueError, match="gateway token"):
        replace(settings, public_origin=ORIGIN)


def test_gateway_and_public_origin_required(private_app):
    with TestClient(private_app, base_url=ORIGIN, client=("127.0.0.1", 1234)) as client:
        assert client.get("/api/health").status_code == 403
        assert client.get("/api/health", headers=GATEWAY).status_code == 200
        assert (
            client.get(
                "/api/health", headers={**GATEWAY, "X-Forwarded-Host": "evil.example"}
            ).status_code
            == 400
        )
        for origin in ("", "https://evil.example", "http://127.0.0.1:3000"):
            assert (
                client.post("/api/x/connect", headers={**GATEWAY, "Origin": origin}).status_code
                == 403
            )
        assert client.post("/api/x/connect", headers=GATEWAY).status_code == 403
        assert (
            client.post(
                "/api/x/connect",
                headers={**GATEWAY, "Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
            ).status_code
            == 403
        )
        assert client.get("http://127.0.0.1:8765/api/health").status_code == 200


def test_nonlocal_peer_cannot_claim_gateway(private_app):
    with TestClient(private_app, base_url=ORIGIN, client=("192.0.2.99", 1234)) as client:
        assert client.get("/api/health", headers=GATEWAY).status_code == 403


def test_unexpected_api_failure_has_safe_reference_and_recovery(private_app, caplog):
    @private_app.post("/api/unexpected")
    def broken():
        raise RuntimeError("private exception details")

    with TestClient(private_app, base_url=ORIGIN, client=("127.0.0.1", 1234)) as client:
        response = client.post("/api/unexpected", headers={**GATEWAY, "Origin": ORIGIN})

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()["detail"]
    assert "Refresh the saved status before trying again" in detail
    reference = detail.split("Error reference ")[1].rstrip(".")
    assert reference in caplog.text
    assert "private exception details" not in detail + caplog.text


def test_stream_forwarded_from_next_uses_same_boundary(private_app, recordings):
    with TestClient(
        private_app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1234)
    ) as client:
        headers = {
            **GATEWAY,
            "X-Forwarded-Host": "cliptric.com",
            "Origin": ORIGIN,
            "Content-Type": "application/octet-stream",
        }
        path = "/api/recordings?filename=test.mp4"
        assert (
            client.post(
                path,
                headers={k: v for k, v in headers.items() if k not in GATEWAY},
                content=b"test",
            ).status_code
            == 403
        )
        assert (
            client.post(path, headers=headers, content=recordings[True].read_bytes()).status_code
            == 201
        )


@pytest.mark.parametrize("platform", ["x", "instagram", "facebook", "tiktok"])
def test_public_oauth_callback_cookie_binding_and_redirect(private_app, platform):
    if __import__("os").name != "nt":
        pytest.skip("Publishing requires Windows credential storage.")
    with TestClient(private_app, base_url=ORIGIN, client=("127.0.0.1", 1234)) as client:
        prefix = f"/api/{platform}"
        response = client.post(f"{prefix}/connect?page_id=3", headers={**GATEWAY, "Origin": ORIGIN})
        assert response.status_code == 200
        query = parse_qs(urlsplit(response.json()["url"]).query)
        assert query["redirect_uri"] == [f"{ORIGIN}{prefix}/callback"]
        assert "Secure" in response.headers["set-cookie"]
        callback = f"{prefix}/callback"
        args = {"state": query["state"][0], "error": "access_denied"}
        assert client.get(callback, params=args).status_code == 403
        result = client.get(
            callback,
            params=args,
            headers={**GATEWAY, "Sec-Fetch-Site": "cross-site"},
            follow_redirects=False,
        )
        assert result.status_code == 303
        assert result.headers["location"] == f"{ORIGIN}/connections?{platform}=cancelled"
        assert client.get(callback, params=args, headers=GATEWAY).status_code == 400
