"""Retrieval stage — self-query: extract date and site filters from a question.

"that article I read last Tuesday about HNSW" becomes
    semantic_query="HNSW article", since=<last Tuesday>, until=<Wednesday>

Everything except the one LLM call is pure and unit-tested. Any failure — bad
JSON, invalid dates, LLM down — degrades to an unfiltered search: this stage
must never be the reason a question goes unanswered.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, List, Optional

from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, ValidationError

import config
from prompts.query_filter_prompt import QUERY_FILTER_PROMPT
from utils.llm import build_chat_model

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_EARLIEST_PLAUSIBLE_YEAR = 2000


class ParsedQuery(BaseModel):
    """A question split into its topical part and its retrieval filters."""

    semantic_query: str
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    domains: Optional[List[str]] = None


def parse_response(raw: str, question: str, now: datetime) -> ParsedQuery:
    """Validate the model's JSON. Anything implausible is dropped, never raised.

    Args:
        raw: The raw model output (JSON, possibly inside a code fence).
        question: The original question, used as the fallback query.
        now: The current time, used to reject impossible date windows.

    Returns:
        The parsed filters, or an unfiltered query when the output is unusable.
    """

    fallback = ParsedQuery(semantic_query=question)

    try:
        data = json.loads(_CODE_FENCE_RE.sub("", raw).strip())
    except (json.JSONDecodeError, TypeError):
        return fallback
    if not isinstance(data, dict):
        return fallback

    try:
        parsed = ParsedQuery(
            semantic_query=str(data.get("semantic_query") or "").strip() or question,
            since=_coerce_datetime(data.get("since"), now),
            until=_coerce_datetime(data.get("until"), now),
            domains=_coerce_domains(data.get("domains")),
        )
    except ValidationError:
        return fallback

    # a window entirely in the future, inverted, or decades old is hallucinated
    if parsed.since is not None and parsed.since > now:
        parsed.since = None
    if parsed.until is not None and parsed.since is not None and parsed.until <= parsed.since:
        parsed.until = None
    if parsed.since is not None and parsed.since.year < _EARLIEST_PLAUSIBLE_YEAR:
        parsed.since = None

    return parsed


def _coerce_datetime(value: Any, now: datetime) -> Optional[datetime]:
    """Parse an ISO datetime string, giving naive values the caller's timezone."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo or timezone.utc)
    return parsed


def _coerce_domains(value: Any) -> Optional[List[str]]:
    """Normalize a list of domains to bare lowercase hosts; None if empty."""

    if not isinstance(value, list):
        return None
    cleaned = [
        domain.strip().lower().removeprefix("www.")
        for domain in value
        if isinstance(domain, str) and domain.strip()
    ]
    return cleaned or None


class QueryFilterService:
    """Runs the filter-extraction chain and validates what it returns."""

    def __init__(self, model=None) -> None:
        """Build the chain (prompt -> chat model -> string parser) once.

        Args:
            model: A chat model or runnable to use instead of the configured
                one (tests inject a fake here).
        """

        model = model or build_chat_model(config.QUERY_FILTER_TEMPERATURE)
        self._chain = QUERY_FILTER_PROMPT | model | StrOutputParser()

    async def parse(self, question: str, now: Optional[datetime] = None) -> ParsedQuery:
        """Extract retrieval filters from a question.

        Args:
            question: The user's question.
            now: The current time; defaults to the local time.

        Returns:
            The topical query plus any date/site filters. Every failure path
            returns an unfiltered query.
        """

        now = now or datetime.now(timezone.utc).astimezone()

        try:
            raw = await self._chain.ainvoke(
                {
                    "weekday": now.strftime("%A"),
                    "today": now.strftime("%Y-%m-%d"),
                    "timezone_offset": now.strftime("%z") or "+0000",
                    "question": question,
                }
            )
        except Exception:
            return ParsedQuery(semantic_query=question)

        return parse_response(raw, question, now)
