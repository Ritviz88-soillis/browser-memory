"""Source formatting for the answer prompt. Pure functions, no I/O.

A ``Source`` is one numbered excerpt shown to the model. Retrieved chunks are
numbered from 1; the page open in the browser right now is always source 0.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence
from urllib.parse import urlparse

CURRENT_PAGE_NUMBER = 0
_NO_TEXT_PLACEHOLDER = "(no readable text could be extracted from this page)"


@dataclass(slots=True)
class Source:
    """One citable excerpt handed to the model."""

    n: int
    title: str
    url: str
    domain: str
    heading_path: List[str]
    visited: datetime
    text: str


def rows_to_sources(rows: Sequence[Any]) -> List[Source]:
    """Adapt hybrid-search rows into numbered sources.

    Args:
        rows: Records returned by ``db.hybrid_search``, best match first.

    Returns:
        Sources numbered from 1 (0 is reserved for the current page).
    """

    return [
        Source(
            n=position + 1,
            title=row["title"] or row["domain"],
            url=row["url"],
            domain=row["domain"],
            heading_path=list(row["heading_path"]),
            visited=row["last_visited_at"],
            text=row["text"],
        )
        for position, row in enumerate(rows)
    ]


def current_page_source(
    url: str,
    title: Optional[str],
    text: Optional[str],
    now: datetime,
) -> Source:
    """Adapt the live browser tab into source [0].

    Args:
        url: URL of the open tab.
        title: Page title, if known.
        text: Readable page text (or a transcript), if any was obtained.
        now: Timestamp recorded as the visit time.

    Returns:
        The current page as a citable source numbered 0.
    """

    domain = urlparse(url).hostname or ""
    return Source(
        n=CURRENT_PAGE_NUMBER,
        title=title or domain or url,
        url=url,
        domain=domain,
        heading_path=[],
        visited=now,
        text=text or _NO_TEXT_PLACEHOLDER,
    )


def format_sources(
    sources: List[Source],
    current_page: Optional[Source] = None,
) -> str:
    """Render the numbered, fenced source block for the answer prompt.

    Args:
        sources: Retrieved sources, numbered from 1.
        current_page: The open tab as source [0], if available.

    Returns:
        The context string placed ahead of the question.
    """

    parts: List[str] = []

    if current_page is not None:
        parts.append(
            f"[{current_page.n}] CURRENTLY OPEN: {current_page.title} ({current_page.domain})\n"
            f"    url: {current_page.url}\n"
            f'    content: """\n{current_page.text}\n"""'
        )

    for source in sources:
        breadcrumb = " > ".join(source.heading_path) if source.heading_path else "-"
        parts.append(
            f"[{source.n}] {source.title} ({source.domain})\n"
            f"    section: {breadcrumb}\n"
            f"    visited: {source.visited:%Y-%m-%d %H:%M}\n"
            f"    url: {source.url}\n"
            f'    content: """\n{source.text}\n"""'
        )

    header = "Sources from the user's browsing history"
    if current_page is not None:
        header += " (source [0] is the page open right now)"

    return header + ":\n\n" + "\n\n".join(parts)
