"""URL normalization shared by the worker and CLI scripts."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

# tracking params stripped for page identity; the raw URL survives on visits
_DROP_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "ref_", "mc_")


def normalize_url(raw: str) -> tuple[str, str]:
    """Return (normalized_url, domain)."""
    parts = urlsplit(raw)
    query = "&".join(
        p for p in parts.query.split("&")
        if p and not p.lower().startswith(_DROP_PARAM_PREFIXES)
    )
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, "")
    )
    return normalized, parts.netloc.lower().removeprefix("www.")
