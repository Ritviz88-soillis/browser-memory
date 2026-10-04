"""Secrets redaction, applied to HTML BEFORE chunk_html — the chunker owns the
canonical text and its offsets, so redaction after it would desynchronize them.

Deterministic patterns only: every pattern is vendor-anchored or checksum-
validated (Luhn), because a false positive silently corrupts the corpus.
Known limitation: a secret split across inline tags ("<b>sk-</b>abc") won't
match; real pages render secrets as contiguous text nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL)),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    # three base64url segments, the first decoding to '{"'
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

_CARD_RE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(slots=True)
class ScrubResult:
    text: str
    findings: dict[str, int]  # kind -> count

    @property
    def total(self) -> int:
        return sum(self.findings.values())


def scrub(text: str) -> ScrubResult:
    """Redact known secret formats; returns scrubbed text plus counts by kind."""
    findings: dict[str, int] = {}

    for kind, pattern in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{kind}]", text)
        if n:
            findings[kind] = findings.get(kind, 0) + n

    def card_repl(m: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            findings["card"] = findings.get("card", 0) + 1
            return "[REDACTED:card]"
        return m.group(0)  # fails Luhn: an ordinary number

    text = _CARD_RE.sub(card_repl, text)
    return ScrubResult(text=text, findings=findings)
