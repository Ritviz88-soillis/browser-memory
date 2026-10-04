"""YouTube URL recognition. Pure — the actual transcript fetch is I/O and
lives in transcripts.py at the server root."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

_HOSTS = {"youtube.com", "youtube-nocookie.com"}
_PATH_PREFIXES = ("/shorts/", "/embed/", "/live/")


def video_id(url: str) -> str | None:
    """Extract the 11-char video id from any YouTube URL shape, else None."""
    try:
        u = urlparse(url)
    except ValueError:
        return None

    host = (u.hostname or "").lower()
    host = host.removeprefix("www.").removeprefix("m.")

    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
        return vid or None

    if host in _HOSTS:
        if u.path == "/watch":
            return parse_qs(u.query).get("v", [None])[0]
        for prefix in _PATH_PREFIXES:
            if u.path.startswith(prefix):
                vid = u.path[len(prefix) :].split("/")[0]
                return vid or None

    return None
