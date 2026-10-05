"""Ingestion: the queue of pages waiting to be indexed, and writing an indexed
page to memory.

Scrubbing, chunking and embedding are separate stages; the orchestrator runs
them and hands the results to ``store``.
"""

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

import config
import db
from schemas import IngestIn, IngestOut


class IngestionService:
    """Queues extracted pages and stores them once they are processed."""

    def enqueue(self, request: IngestIn, device_id: str) -> IngestOut:
        """Record a page for background indexing.

        Args:
            request: The extracted page and its visit details.
            device_id: The device that read the page.

        Returns:
            Whether the job was queued or already existed (idempotent retry).

        Raises:
            ValueError: If the request asks for a different embedding model
                than the one this memory was built with.
        """

        if request.model != config.EMBEDDING_MODEL:
            raise ValueError(
                f"this memory uses {config.EMBEDDING_MODEL!r}, not {request.model!r}"
            )

        payload = request.model_dump(mode="json") | {"device_id": device_id}
        newly_queued = db.enqueue(request.idempotency_key, payload)
        return IngestOut(queued=True, duplicate=not newly_queued)

    def is_blocked(self, domain: str) -> bool:
        """Whether the user asked to forget this site (it is never indexed).

        Args:
            domain: The page's site.

        Returns:
            True if pages from the site must be skipped.
        """

        return db.is_blocked(domain)

    def record_revisit(
        self,
        url: str,
        extracted_text: str,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        """If the page is already indexed with the same content, record this
        visit and report that nothing needs re-indexing.

        Args:
            url: The page's normalized URL.
            extracted_text: The page's text as just extracted.
            payload: The job payload (for the device, raw URL and visit).

        Returns:
            The page id when the page was unchanged, otherwise None.
        """

        existing = db.find_page(url)
        if not existing or existing["content_hash"] != db.content_hash(extracted_text):
            return None

        db.record_visit(
            existing["id"],
            payload.get("device_id"),
            payload["url"],
            self._parse_visit(payload["visit"]),
        )
        return existing["id"]

    def store(
        self,
        payload: Dict[str, Any],
        url: str,
        domain: str,
        extracted_text: str,
        chunks: Sequence[Any],
        vectors: Sequence[Sequence[float]],
    ) -> str:
        """Write a new or changed page, its chunks and the visit in one transaction.

        Args:
            payload: The job payload (title, language, device, raw URL, visit).
            url: The page's normalized URL.
            domain: The page's site.
            extracted_text: The canonical text the chunks were cut from.
            chunks: The page's chunks, in page order.
            vectors: One embedding per chunk.

        Returns:
            The page id.
        """

        return db.save_page(
            device_id=payload.get("device_id"),
            domain=domain,
            url=url,
            raw_url=payload["url"],
            title=payload.get("title"),
            lang=payload.get("lang"),
            extracted_text=extracted_text,
            chunks=[
                db.ChunkRow(
                    ordinal=chunk.ordinal,
                    heading_path=chunk.heading_path,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding=vector,
                )
                for chunk, vector in zip(chunks, vectors)
            ],
            visit=self._parse_visit(payload["visit"]),
        )

    def _parse_visit(self, visit: Dict[str, Any]) -> Dict[str, Any]:
        """Turn the visit's ISO timestamp (JSON payload) back into a datetime."""

        parsed = dict(visit)
        parsed["started_at"] = datetime.fromisoformat(parsed["started_at"])
        return parsed
