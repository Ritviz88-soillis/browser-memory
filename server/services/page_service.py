"""The indexed-pages panel: status counters, listing, and forgetting."""

from typing import List, Optional

import config
import db
from schemas import PageOut, StatusOut


class PageService:
    """Reads and deletes what is in memory."""

    async def status(self) -> StatusOut:
        """Return the live indexing counters.

        Returns:
            Pending/failed job counts and the number of pages and chunks.
        """

        return StatusOut(**db.status_counts())

    async def list_pages(
        self,
        limit: int,
        offset: int,
        search: Optional[str],
    ) -> List[PageOut]:
        """List indexed pages, most recently visited first.

        Args:
            limit: Maximum pages to return (capped by config).
            offset: How many pages to skip.
            search: Optional substring to match against title or URL.

        Returns:
            The matching pages.
        """

        rows = db.list_pages(
            limit=min(limit, config.PAGES_MAX_LIMIT),
            offset=offset,
            q=search,
        )
        return [
            PageOut(
                id=str(row["id"]),
                url=row["url"],
                domain=row["domain"],
                title=row["title"],
                word_count=row["word_count"],
                last_visited_at=row["last_visited_at"],
                indexed_at=row["indexed_at"],
            )
            for row in rows
        ]

    async def forget_page(self, page_id: str) -> bool:
        """Delete one page and everything derived from it.

        Args:
            page_id: The page's id.

        Returns:
            True if the page existed and was deleted.
        """

        return db.delete_page(page_id)

    async def forget_site(self, domain: str) -> int:
        """Block a domain and delete every page indexed from it.

        Args:
            domain: The site's domain.

        Returns:
            The number of pages deleted.
        """

        return db.forget_site(domain.lower().removeprefix("www."))
