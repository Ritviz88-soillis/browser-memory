"""The indexing brain: sequences scrub -> chunk -> embed -> store.

Ingestion is split in two so the extension never waits on embedding:
``enqueue`` (called by the API) only records a job, and ``process`` (called by
the background worker) does the actual indexing.
"""

from datetime import datetime
from typing import Any, Dict, Optional

import config
import db
from schemas import IngestIn, IngestOut
from services.chunking_service import ChunkingService
from services.embedding_service import get_embedder
from utils.scrub import scrub
from utils.urls import normalize_url


class IngestionService:
    """Turns an extracted page into searchable, embedded chunks."""

    def __init__(self) -> None:
        """Instantiate the stage services once (reused across pages)."""

        self._chunking = ChunkingService()

    async def enqueue(self, request: IngestIn, device_id: str) -> IngestOut:
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

    async def process(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Index one queued page.

        Args:
            payload: The job payload written by ``enqueue``.

        Returns:
            The page id and whether new content was indexed, or None when the
            page was skipped (blocked site, or nothing readable).
        """

        url, domain = normalize_url(payload["url"])
        if db.is_blocked(domain):
            return None

        # 1. Redact secrets BEFORE chunking: the chunker owns the canonical
        #    text, so redacting afterwards would desynchronize chunk offsets
        scrubbed = scrub(payload["html"])

        # 2. Split into heading-aware chunks
        extracted_text, chunks = self._chunking.chunk(scrubbed.text)
        if not chunks:
            return None

        visit = self._parse_visit(payload["visit"])
        device_id = payload.get("device_id")

        # 3. Unchanged page seen again: record the visit, skip the embedding
        existing = db.find_page(url)
        if existing and existing["content_hash"] == db.content_hash(extracted_text):
            db.record_visit(existing["id"], device_id, payload["url"], visit)
            return {"page_id": existing["id"], "new_content": False}

        # 4. Embed every chunk of the page in one batch
        embedder = get_embedder(config.EMBEDDING_MODEL)
        vectors = await embedder.embed_passages([chunk.text for chunk in chunks])

        # 5. Store page, chunks and visit in one transaction
        page_id = db.save_page(
            device_id=device_id,
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
            visit=visit,
        )
        return {"page_id": page_id, "new_content": True}

    def _parse_visit(self, visit: Dict[str, Any]) -> Dict[str, Any]:
        """Turn the visit's ISO timestamp (JSON payload) back into a datetime."""

        parsed = dict(visit)
        parsed["started_at"] = datetime.fromisoformat(parsed["started_at"])
        return parsed
