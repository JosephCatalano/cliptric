"""Bounded HTTPS transport and typed records for X and Facebook publishing."""

import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx2
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT


class SocialError(ValueError):
    def __init__(self, message, status_code=502, *, provider_status=None):
        super().__init__(message)
        self.status_code = status_code
        self.provider_status = provider_status


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}", value):
        raise SocialError("The provider returned an invalid identifier.")
    return value


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    refresh_token: str = Field(default="", max_length=8192, repr=False)
    subject: str = Field(pattern=r"^[0-9]{1,32}$")
    label: str = Field(min_length=1, max_length=300)
    expires_at: float = Field(gt=0)
    scope: str = Field(default="", max_length=1024)


@dataclass(frozen=True)
class SocialSettings:
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    login_config_id: str = ""
    version: str = "v27.0"

    def __post_init__(self):
        if not re.fullmatch(r"v[0-9]{1,3}\.0", self.version):
            raise ValueError("Facebook API version must have the form v27.0.")
        if self.login_config_id and not re.fullmatch(r"[0-9]{1,32}", self.login_config_id):
            raise ValueError("Facebook login configuration ID must be numeric.")
        if self.login_config_id and not re.fullmatch(r"[0-9]{1,32}", self.client_id):
            raise ValueError("Facebook app ID must be numeric.")

    @classmethod
    def load(cls, platform):
        if platform not in {"x", "facebook"}:
            raise ValueError("Unsupported publishing platform")
        values = dotenv_values(ROOT / "backend" / ".env", interpolate=False)
        prefix = platform.upper()

        def get(name, default=""):
            return (
                os.environ.get(f"{prefix}_{name}", values.get(f"{prefix}_{name}")) or default
            ).strip()

        return cls(
            get("CLIENT_ID" if platform == "x" else "APP_ID"),
            get("CLIENT_SECRET" if platform == "x" else "APP_SECRET"),
            get("LOGIN_CONFIG_ID"),
            get("API_VERSION", "v27.0"),
        )


class SensitiveSocialLogs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if re.search(r"/api/(?:x|facebook)/callback\?", message):
            record.msg = re.sub(
                r"(/api/(?:x|facebook)/callback)\?[^\s\"]*", r"\1?[redacted]", message
            )
            record.args = ()
        elif any(
            host in message for host in ("api.x.com", "graph.facebook.com", "rupload.facebook.com")
        ):
            record.msg, record.args = "Social API request (credentials redacted)", ()
        return True


def secure_social_logs():
    for name in ("uvicorn.access", "httpx2", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, SensitiveSocialLogs) for item in logger.filters):
            logger.addFilter(SensitiveSocialLogs())


class Transport:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    def send(
        self,
        method,
        url,
        *,
        headers=None,
        params=None,
        form=None,
        body=None,
        content=None,
        timeout=60,
    ):
        # Callers construct URLs from constants and validated numeric identifiers.
        try:
            with httpx2.Client(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=timeout
            ) as client:
                with client.stream(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    data=form,
                    json=body,
                    content=content,
                ) as response:
                    if response.status_code not in {200, 201, 202, 204}:
                        status = response.status_code
                        guidance = {
                            400: "Review the post details and media requirements.",
                            401: "Reconnect the account and review its access.",
                            402: "Check the developer app’s API credits.",
                            403: "Check that the developer app and account have publishing permission.",
                            429: "Wait for the provider’s rate limit to reset.",
                        }.get(status, "Check saved publishing status before retrying.")
                        raise SocialError(
                            f"The publishing provider rejected the request (HTTP {status}). {guidance}",
                            429 if status == 429 else 502,
                            provider_status=status,
                        )
                    payload = bytearray()
                    for part in response.iter_bytes(chunk_size=8192):
                        payload.extend(part)
                        if len(payload) > 128 * 1024:
                            raise SocialError("The provider returned an oversized response.")
            result = json.loads(payload) if payload else {}
            if not isinstance(result, dict) or result.get("error") or result.get("errors"):
                raise SocialError("The provider returned an unsuccessful response.")
            return result
        except (httpx2.HTTPError, ValueError, UnicodeError) as exc:
            if isinstance(exc, SocialError):
                raise
            raise SocialError(
                "The provider request failed. Check saved publishing status before retrying."
            ) from exc
