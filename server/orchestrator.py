"""Delegation between the HTTP router and the services (mirrors t1's orchestrator)."""

import logging
from typing import Any, Dict, List, Optional

import db
import worker
from schemas import (
    AskIn,
    AskOut,
    CurrentPageIn,
    IngestIn,
    IngestOut,
    PageOut,
    RelatedOut,
    StatusOut,
)
from services.ingestion_service import IngestionService
from services.page_service import PageService
from services.rag_service import RAGService
from services.recall_service import RecallService

logger = logging.getLogger(__name__)


class MemoryOrchestrator:
    """Orchestrator for the browser-memory module.

    Dispatches each request to the service that owns it; holds no logic.
    """

    def __init__(self) -> None:
        """Create the underlying services."""

        self._ingestion = IngestionService()
        self._rag = RAGService()
        self._recall = RecallService()
        self._pages = PageService()

    async def health_check(self) -> Dict[str, bool]:
        """Module health status (verifies the database is reachable)."""

        db.ping()
        return {"ok": True}

    async def ingest(self, request: IngestIn, device_id: str) -> IngestOut:
        """Queue a page for background indexing.

        Args:
            request: The extracted page and its visit details.
            device_id: The device that read the page.

        Returns:
            Whether the page was queued or was a duplicate.
        """

        logger.info("MemoryOrchestrator: queueing '%s'", request.url)
        result = await self._ingestion.enqueue(request, device_id)
        worker.notify()
        return result

    async def process_job(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Index one queued page (called by the background worker).

        Args:
            payload: The job payload written at ingest time.

        Returns:
            The page id and whether new content was indexed, or None when the
            page was skipped.
        """

        return await self._ingestion.process(payload)

    async def ask(self, request: AskIn, device_id: Optional[str]) -> AskOut:
        """Answer a question from the user's browsing memory.

        Args:
            request: The question, conversation history and current page.
            device_id: The asking device.

        Returns:
            The grounded answer with its cited sources.
        """

        return await self._rag.answer(request, device_id)

    async def related(self, page: CurrentPageIn) -> RelatedOut:
        """Find previously read pages related to the page open now.

        Args:
            page: The open tab: its URL, title and readable text.

        Returns:
            Related pages above the similarity cutoff, most similar first.
        """

        return await self._recall.related(page)

    async def status(self) -> StatusOut:
        """Return the live indexing counters."""

        return await self._pages.status()

    async def list_pages(
        self,
        limit: int,
        offset: int,
        search: Optional[str],
    ) -> List[PageOut]:
        """List indexed pages, most recently visited first.

        Args:
            limit: Maximum pages to return.
            offset: How many pages to skip.
            search: Optional substring to match against title or URL.

        Returns:
            The matching pages.
        """

        return await self._pages.list_pages(limit, offset, search)

    async def forget_page(self, page_id: str) -> bool:
        """Delete one page; returns False if it did not exist."""

        return await self._pages.forget_page(page_id)

    async def forget_site(self, domain: str) -> int:
        """Block a domain and delete its pages; returns how many were deleted."""

        return await self._pages.forget_site(domain)
