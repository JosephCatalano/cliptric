"""Facebook Login for Business and Page Reels, using local video bytes."""

import time
from urllib.parse import urlencode

from .social_transport import Credentials, SocialError, Transport, identifier


class FacebookAPI(Transport):
    platform, name = "facebook", "Facebook"

    def authorization(self, state, challenge, redirect, page_id):
        return f"https://www.facebook.com/{self.settings.version}/dialog/oauth?" + urlencode(
            {
                "client_id": self.settings.client_id,
                "config_id": self.settings.login_config_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "state": state,
            }
        )

    def request(self, path, *, token=None, params=None, form=None, method="GET", timeout=60):
        import re

        if not re.fullmatch(
            r"(?:[0-9]{1,32}(?:/video_reels)?|oauth/access_token|me/accounts)", path
        ):
            raise ValueError("Unsupported Facebook endpoint")
        return self.send(
            method,
            f"https://graph.facebook.com/{self.settings.version}/{path}",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            params=params,
            form=form,
            timeout=timeout,
        )

    def connect(self, code, verifier, redirect, page_id):
        result = self.request(
            "oauth/access_token",
            params={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "redirect_uri": redirect,
                "code": code,
            },
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise SocialError("Facebook returned invalid credentials.")
        extended = self.request(
            "oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "fb_exchange_token": token,
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
            },
        )
        token, expiry = extended.get("access_token"), extended.get("expires_in")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or type(expiry) is not int
            or not 0 < expiry <= 31536000
        ):
            raise SocialError("Facebook did not return valid long-lived authorization.")
        identifier(page_id)
        page, after = None, None
        # Page tasks belong to the User/accounts edge. Follow only validated
        # cursors at our fixed endpoint, never a provider-supplied paging URL.
        for _ in range(10):
            params = {"fields": "id,name,access_token,tasks", "limit": "100"}
            if after:
                params["after"] = after
            result = self.request("me/accounts", token=token, params=params)
            pages = result.get("data")
            if (
                not isinstance(pages, list)
                or len(pages) > 100
                or any(not isinstance(item, dict) for item in pages)
            ):
                raise SocialError("Facebook returned an invalid Page list.")
            page = next((item for item in pages if item.get("id") == page_id), None)
            if page:
                break
            paging = result.get("paging", {})
            if not isinstance(paging, dict) or not paging.get("next"):
                break
            cursors = paging.get("cursors", {})
            cursor = cursors.get("after") if isinstance(cursors, dict) else None
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096 or cursor == after:
                raise SocialError("Facebook returned an invalid Page-list cursor.")
            after = cursor
        try:
            if (
                not page
                or not isinstance(page.get("tasks"), list)
                or not {
                    "CREATE_CONTENT",
                    "MANAGE",
                    "PROFILE_PLUS_CREATE_CONTENT",
                    "PROFILE_PLUS_FULL_CONTROL",
                }.intersection(page["tasks"])
            ):
                raise ValueError("Page publishing access missing")
            return Credentials(
                subject=page_id,
                access_token=page["access_token"],
                label=page["name"],
                expires_at=time.time() + expiry,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SocialError(
                "Authorize a Facebook Page where you have permission to create content.", 409
            ) from exc

    def identity(self, record):
        page = self.request(record.subject, token=record.access_token, params={"fields": "id,name"})
        if (
            page.get("id") != record.subject
            or not isinstance(page.get("name"), str)
            or not 1 <= len(page["name"]) <= 300
        ):
            raise SocialError(
                "The Facebook Page identity changed. Reconnect the original Page.", 409
            )
        return page["name"]

    def refresh(self, record):
        raise SocialError(
            "Facebook authorization expired. Reconnect this Page before publishing.", 409
        )

    def prepare(self, record, path, size, stop, progress):
        result = self.request(
            f"{record.subject}/video_reels",
            token=record.access_token,
            method="POST",
            form={"upload_phase": "start"},
        )
        media_id = identifier(result.get("video_id"))
        # Ignore returned upload_url; never send a Page token to an arbitrary host.
        deadline = time.monotonic() + 1800
        with path.open("rb") as stream:

            def content():
                sent = reported = 0
                while sent < size:
                    if stop.is_set() or time.monotonic() > deadline:
                        raise SocialError("Facebook upload stopped or exceeded its time budget.")
                    chunk = stream.read(min(256 * 1024, size - sent))
                    if not chunk:
                        raise SocialError("The export became unreadable during upload.")
                    yield chunk
                    sent += len(chunk)
                    if sent - reported >= 4 * 1024**2 or sent == size:
                        progress(sent)
                        reported = sent

            result = self.send(
                "POST",
                f"https://rupload.facebook.com/video-upload/{self.settings.version}/{media_id}",
                headers={
                    "Authorization": f"OAuth {record.access_token}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Length": str(size),
                    "Content-Type": "application/octet-stream",
                },
                content=content(),
            )
        if result.get("success") is not True:
            raise SocialError("Facebook did not confirm the video upload.")
        return media_id

    def publish(self, record, media_id, request):
        result = self.request(
            f"{record.subject}/video_reels",
            token=record.access_token,
            method="POST",
            form={
                "upload_phase": "finish",
                "video_id": media_id,
                "video_state": "PUBLISHED",
                "description": request.caption,
                "is_ai_generated": str(request.is_ai_generated).lower(),
            },
        )
        if result.get("success") is not True:
            raise SocialError("Facebook did not confirm publication. Check saved status.")
        return media_id

    def published(self, record, media_id, post_id):
        if not media_id:
            return False
        status = self.request(
            identifier(media_id), token=record.access_token, params={"fields": "status"}
        ).get("status", {})
        phase = status.get("publishing_phase", {})
        if phase.get("status") == "error" or status.get("video_status") == "error":
            raise SocialError(
                "Facebook reports a video processing or publishing error. Check the Page before retrying."
            )
        return phase.get("publish_status") == "published"
