"""Single-video URLs and an HTTPS-only downloader transport pinned to yt-dlp 2026.10.26."""

import ipaddress
import re
import socket
import urllib.request
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking._urllib import UrllibRH


def canonical_video_url(value: str) -> str:
    url = urlsplit(value.strip())
    if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
        raise ValueError("Use an HTTPS link to a single YouTube video.")
    if url.hostname == "youtu.be":
        identifier = url.path.removeprefix("/")
    elif url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        parts = url.path.strip("/").split("/")
        if url.path == "/watch":
            values = parse_qs(url.query).get("v", [])
            identifier = values[0] if len(values) == 1 else ""
        else:
            identifier = (
                parts[1] if len(parts) == 2 and parts[0] in {"shorts", "embed", "live"} else ""
            )
    else:
        raise ValueError("Use a youtube.com or youtu.be video link.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise ValueError("This link does not identify a single YouTube video.")
    return f"https://www.youtube.com/watch?v={identifier}"


def validate_destination(value: str):
    url = urlsplit(value)
    host = url.hostname or ""
    allowed = ("youtube.com", "googlevideo.com", "youtubei.googleapis.com", "ytimg.com")
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.port not in (None, 443)
        or not any(host == domain or host.endswith("." + domain) for domain in allowed)
    ):
        raise ValueError("YouTube import blocked an unsupported network destination.")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global for address in addresses
    ):
        raise ValueError("YouTube import blocked a non-public network destination.")


class DestinationGuard(urllib.request.BaseHandler):
    def default_open(self, request):
        validate_destination(request.full_url)
        return None


class YouTubeTransportRH(UrllibRH):
    _SUPPORTED_URL_SCHEMES = ("https",)

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        # The processor also runs on redirected requests before a connection opens.
        opener = super()._create_instance({}, cookiejar, legacy_ssl_support)
        opener.add_handler(DestinationGuard())
        return opener


class YouTubeDownloader(YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        # Do not fall back to transports that lack the destination guard.
        return super().build_request_director([YouTubeTransportRH])
