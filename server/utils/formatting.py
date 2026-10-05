"""Source formatting for the answer prompt. Pure functions, no I/O.

A ``Source`` is one numbered passage shown to the model. Passages from pages
open in the browser right now ("live" sources) are listed first, followed by
passages retrieved from memory. Numbering is continuous, starting at 1.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence
from urllib.parse import urlparse


@dataclass(slots=True)
class Source:
    """One citable passage handed to the model."""

    n: int
    title: str
    url: str
    domain: str
    heading_path: List[str]
    visited: datetime
    text: str
    live: bool = False            # from a tab that is open right now
    tab_id: Optional[int] = None  # which tab, so the extension can highlight in it


def live_page_sources(
    url: str,
    title: Optional[str],
    tab_id: Optional[int],
    passages: Sequence[Any],
    now: datetime,
    start: int = 1,
) -> List[Source]:
    """Turn the selected passages of an open page into numbered sources.

    Args:
        url: URL of the open tab.
        title: Page title, if known.
        tab_id: The browser's id for the tab.
        passages: Chunks chosen for this question (``heading_path`` and ``text``).
        now: Timestamp recorded as the visit time.
        start: Number given to the first passage.

    Returns:
        One live source per passage, numbered from ``start``.
    """

    domain = urlparse(url).hostname or ""
    return [
        Source(
            n=start + position,
            title=title or domain or url,
            url=url,
            domain=domain,
            heading_path=list(passage.heading_path),
            visited=now,
            text=passage.text,
            live=True,
            tab_id=tab_id,
        )
        for position, passage in enumerate(passages)
    ]


def rows_to_sources(rows: Sequence[Any], start: int = 1) -> List[Source]:
    """Adapt hybrid-search rows into numbered sources.

    Args:
        rows: Records returned by ``db.hybrid_search``, best match first.
        start: Number given to the first row (after any live sources).

    Returns:
        Memory sources numbered from ``start``.
    """

    return [
        Source(
            n=start + position,
            title=row["title"] or row["domain"],
            url=row["url"],
            domain=row["domain"],
            heading_path=list(row["heading_path"]),
            visited=row["last_visited_at"],
            text=row["text"],
        )
        for position, row in enumerate(rows)
    ]


def format_sources(sources: List[Source]) -> str:
    """Render the numbered, fenced source block for the answer prompt.

    Args:
        sources: Live sources followed by memory sources.

    Returns:
        The context string placed ahead of the question.
    """

    parts: List[str] = []

    # number the open tabs, so that when several are being compared the model
    # can see which passages belong together ("OPEN TAB 2 of 3")
    open_tabs: List[tuple] = []
    for source in sources:
        tab = (source.tab_id, source.url)
        if source.live and tab not in open_tabs:
            open_tabs.append(tab)

    for source in sources:
        breadcrumb = " > ".join(source.heading_path) if source.heading_path else "-"
        if source.live:
            label = "OPEN TAB"
            if len(open_tabs) > 1:
                position = open_tabs.index((source.tab_id, source.url)) + 1
                label = f"OPEN TAB {position} of {len(open_tabs)}"
            parts.append(
                f"[{source.n}] {label}: {source.title} ({source.domain})\n"
                f"    section: {breadcrumb}\n"
                f"    url: {source.url}\n"
                f'    content: """\n{source.text}\n"""'
            )
        else:
            parts.append(
                f"[{source.n}] {source.title} ({source.domain})\n"
                f"    section: {breadcrumb}\n"
                f"    visited: {source.visited:%Y-%m-%d %H:%M}\n"
                f"    url: {source.url}\n"
                f'    content: """\n{source.text}\n"""'
            )

    header = "Sources"
    if any(source.live for source in sources):
        header += " (OPEN TAB sources are from pages open in the browser right now)"

    return header + ":\n\n" + "\n\n".join(parts)
