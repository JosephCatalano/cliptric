"""X OAuth 2.0 PKCE and API v2 chunked video publishing."""

import base64
import time
from urllib.parse import urlencode

from .social_transport import Credentials, SocialError, Transport, identifier

SCOPES = {"tweet.read", "tweet.write", "users.read", "media.write", "offline.access"}


class XAPI(Transport):
    platform, name = "x", "X"

    def authorization(self, state, challenge, redirect, page_id):
        return "https://x.com/i/oauth2/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": redirect,
                "scope": " ".join(sorted(SCOPES)),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    def request(self, path, token, *, body=None, params=None, method="GET", timeout=60):
        import re

        if not re.fullmatch(
            r"(?:users/me|tweets|tweets/[0-9]{1,32}|media/upload(?:/initialize|/[0-9]{1,32}/(?:append|finalize))?)",
            path,
        ):
            raise ValueError("Unsupported X endpoint")
        try:
            return self.send(
                method,
                f"https://api.x.com/2/{path}",
                headers={"Authorization": f"Bearer {token}"},
                body=body,
                params=params,
                timeout=timeout,
            )
        except SocialError as exc:
            if exc.provider_status is None:
                raise
            operation = {
                "initialize": "starting the video upload",
                "append": "sending a video chunk",
                "finalize": "finalizing the video upload",
                "upload": "checking video processing",
                "tweets": "creating the post",
            }.get(path.rsplit("/", 1)[-1], "checking account or post access")
            guidance = {
                400: "Check the export format and post details against X’s requirements.",
                401: "Reconnect the X account and review its access.",
                402: "Check the app’s API credits in the X Developer Console.",
                403: "Check app permissions and account access in the X Developer Console.",
                429: "Wait for the X API rate limit to reset before reviewing a retry.",
            }.get(exc.provider_status, "Check X API access and saved publishing status.")
            raise SocialError(
                f"X rejected the request while {operation} (HTTP {exc.provider_status}). {guidance}",
                exc.status_code,
                provider_status=exc.provider_status,
            ) from exc

    def token(self, **form):
        basic = base64.b64encode(
            f"{self.settings.client_id}:{self.settings.client_secret}".encode()
        ).decode()
        return self.send(
            "POST",
            "https://api.x.com/2/oauth2/token",
            headers={"Authorization": f"Basic {basic}"},
            form=form,
        )

    def identity(self, record):
        profile = self.request("users/me", record.access_token).get("data", {})
        subject = identifier(profile.get("id"))
        username = profile.get("username")
        if (
            not isinstance(username, str)
            or not 1 <= len(username) <= 100
            or subject != record.subject
        ):
            raise SocialError(
                "The X account identity changed. Reconnect the original account.", 409
            )
        return "@" + username

    def _record(self, response, previous=None):
        try:
            expiry = response["expires_in"]
            scope = response.get("scope", previous.scope if previous else "")
            if (
                type(expiry) is not int
                or not 0 < expiry <= 31536000
                or not SCOPES.issubset(scope.split())
            ):
                raise ValueError("Missing permissions or expiry")
            token = response["access_token"]
            profile = self.request("users/me", token).get("data", {})
            subject = identifier(profile.get("id"))
            if previous and subject != previous.subject:
                raise ValueError("Identity changed")
            record = Credentials(
                access_token=token,
                refresh_token=response.get(
                    "refresh_token", previous.refresh_token if previous else ""
                ),
                subject=subject,
                label="@" + profile["username"],
                expires_at=time.time() + expiry,
                scope=scope,
            )
            if not record.refresh_token:
                raise ValueError("Offline access missing")
            return record
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SocialError(
                "X did not return valid account credentials and all required permissions. Reconnect.",
                409,
            ) from exc

    def connect(self, code, verifier, redirect, page_id):
        return self._record(
            self.token(
                grant_type="authorization_code",
                code=code,
                code_verifier=verifier,
                redirect_uri=redirect,
            )
        )

    def refresh(self, record):
        return self._record(
            self.token(grant_type="refresh_token", refresh_token=record.refresh_token), record
        )

    def prepare(self, record, path, size, stop, progress):
        response = self.request(
            "media/upload/initialize",
            record.access_token,
            method="POST",
            body={"media_type": "video/mp4", "media_category": "tweet_video", "total_bytes": size},
        )
        media_id = identifier(response.get("data", {}).get("id"))
        deadline, sent, index = time.monotonic() + 1800, 0, 0
        with path.open("rb") as stream:
            while sent < size:
                if stop.is_set() or time.monotonic() > deadline:
                    raise SocialError("X upload stopped or exceeded its time budget.")
                chunk = stream.read(min(3 * 1024**2, size - sent))
                if not chunk:
                    raise SocialError("The export became unreadable during upload.")
                self.request(
                    f"media/upload/{media_id}/append",
                    record.access_token,
                    method="POST",
                    body={"segment_index": index, "media": base64.b64encode(chunk).decode("ascii")},
                )
                sent += len(chunk)
                index += 1
                progress(sent)
        response = self.request(
            f"media/upload/{media_id}/finalize", record.access_token, method="POST"
        )
        deadline = time.monotonic() + 300
        while True:
            data = response.get("data")
            if not isinstance(data, dict) or data.get("id") != media_id:
                raise SocialError("X returned invalid media processing status.")
            info = data.get("processing_info")
            if info is None or (isinstance(info, dict) and info.get("state") == "succeeded"):
                return media_id
            if not isinstance(info, dict) or info.get("state") not in {"pending", "in_progress"}:
                raise SocialError("X rejected the uploaded video.")
            delay = info.get("check_after_secs", 5)
            if type(delay) is not int or not 0 <= delay <= 300:
                raise SocialError("X returned an invalid processing delay.")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or stop.wait(min(max(1, delay), remaining)):
                raise SocialError("X video processing stopped or exceeded five minutes.")
            response = self.request(
                "media/upload",
                record.access_token,
                params={"media_id": media_id, "command": "STATUS"},
            )

    def publish(self, record, media_id, request):
        result = self.request(
            "tweets",
            record.access_token,
            method="POST",
            body={"text": request.caption, "media": {"media_ids": [media_id]}},
        )
        return identifier(result.get("data", {}).get("id"))

    def published(self, record, media_id, post_id):
        if not post_id:
            return False
        return (
            self.request(f"tweets/{identifier(post_id)}", record.access_token)
            .get("data", {})
            .get("id")
            == post_id
        )
