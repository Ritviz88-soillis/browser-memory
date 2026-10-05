"""The indexed-pages panel: status counters, listing, and forgetting."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import config
import db
from schemas import PageOut, StatusOut


@dataclass(slots=True)
class StoredPassage:
    """One passage of an indexed page."""

    text: str
    heading_path: List[str]


class PageService:
    """Reads and deletes what is in memory."""

    def ping(self) -> None:
        """Check the memory database is usable (raises if it is not)."""

        db.ping()

    def stored_text(self, url: str) -> Optional[str]:
        """Return the text of an already-indexed page.

        Args:
            url: The page's normalized URL.

        Returns:
            The stored text, or None if the page is not in memory.
        """

        return db.page_text_for_url(url)

    def stored_passages(self, url: str) -> Optional[Tuple[str, List[StoredPassage], List[List[float]]]]:
        """Return an indexed page's passages exactly as they were stored.

        Used when the open tab cannot be read live (a PDF in the browser's
        viewer): its indexed passages keep their section headings, such as
        the page number, and need no re-embedding.

        Args:
            url: The page's normalized URL.

        Returns:
            The content fingerprint, the passages in page order and their
            embeddings; None if the page is not in memory.
        """

        stored = db.page_chunks(url)
        if stored is None or not stored["chunks"]:
            return None
        passages = [
            StoredPassage(text=chunk["text"], heading_path=chunk["heading_path"])
            for chunk in stored["chunks"]
        ]
        vectors = [chunk["embedding"] for chunk in stored["chunks"]]
        return stored["content_hash"], passages, vectors

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
