"""Ingestion: the queue of pages waiting to be indexed, and writing an indexed
page to memory.

Scrubbing, chunking and embedding are separate stages; the orchestrator runs
them and hands the results to ``store``.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import config
import db
from schemas import IngestIn, IngestOut, IngestPdfIn


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

    # --- PDFs: a parked file, indexed in batches ---------------------------

    def enqueue_pdf(self, request: IngestPdfIn, data: bytes, device_id: str) -> IngestOut:
        """Park a PDF on disk and queue a job that points at it.

        The file is not read here, so the extension gets its reply at once;
        the worker reads it a few pages at a time.

        Args:
            request: The PDF's URL, title and visit details.
            data: The file's bytes.
            device_id: The device that read it.

        Returns:
            Whether the job was queued or already existed (idempotent retry).
        """

        config.PDF_SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:32]
        path = config.PDF_SPOOL_DIR / f"{name}.pdf"
        path.write_bytes(data)

        payload = {
            "kind": "pdf",
            "device_id": device_id,
            "url": request.url,
            "title": request.title,
            "visit": request.visit.model_dump(mode="json"),
            "pdf_path": str(path),
            # identifies this exact file, so reopening it is not re-indexed
            "fingerprint": "pdf:" + hashlib.sha256(data).hexdigest(),
        }
        newly_queued = db.enqueue(request.idempotency_key, payload)
        return IngestOut(queued=True, duplicate=not newly_queued)

    def record_pdf_revisit(self, url: str, payload: Dict[str, Any]) -> Optional[str]:
        """If this exact file is already fully indexed at this URL, record the
        visit and report that nothing needs indexing.

        Args:
            url: The PDF's normalized URL.
            payload: The job payload (fingerprint, device, raw URL, visit).

        Returns:
            The page id when the file was already indexed, otherwise None.
        """

        existing = db.find_page(url)
        if not existing or existing["content_hash"] != payload["fingerprint"]:
            return None

        db.record_visit(
            existing["id"],
            payload.get("device_id"),
            payload["url"],
            self._parse_visit(payload["visit"]),
        )
        return existing["id"]

    def store_pdf_batch(
        self,
        job_id: Optional[int],
        payload: Dict[str, Any],
        url: str,
        domain: str,
        extracted_text: str,
        chunks: Sequence[Any],
        vectors: Sequence[Sequence[float]],
        pages_done: int,
    ) -> Dict[str, Any]:
        """Save one batch of pages and how far the job has got, together.

        Chunk numbers and text offsets continue from the batches already
        stored, so the document reads as one page however many batches it took.

        Args:
            job_id: The queue job (None when run outside the queue).
            payload: The job payload; its ``progress`` is replaced.
            url: The PDF's normalized URL.
            domain: The PDF's site.
            extracted_text: This batch's canonical text.
            chunks: This batch's chunks, in order.
            vectors: One embedding per chunk.
            pages_done: How many pages are finished once this batch is saved.

        Returns:
            The updated progress: pages done, page id, chunks and text so far.
        """

        progress = payload["progress"]
        # later batches are appended after a blank line
        offset = progress["text_chars"] + 2 if progress["text_chars"] else 0

        rows = [
            db.ChunkRow(
                ordinal=progress["chunks"] + position,
                heading_path=chunk.heading_path,
                text=chunk.text,
                token_count=chunk.token_count,
                char_start=chunk.char_start + offset,
                char_end=chunk.char_end + offset,
                embedding=vector,
            )
            for position, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]

        payload["progress"] = {
            **progress,
            "pages_done": pages_done,
            "chunks": progress["chunks"] + len(rows),
            "text_chars": offset + len(extracted_text) if rows else progress["text_chars"],
        }
        payload["progress"]["page_id"] = db.save_document_batch(
            job_id=job_id,
            payload=payload,
            page_id=progress["page_id"],
            device_id=payload.get("device_id"),
            domain=domain,
            url=url,
            raw_url=payload["url"],
            title=payload.get("title"),
            text=extracted_text,
            chunks=rows,
            visit=self._parse_visit(payload["visit"]),
        )
        return payload["progress"]

    def finish_pdf(self, payload: Dict[str, Any]) -> None:
        """Mark a PDF as fully indexed and remove its parked file.

        Args:
            payload: The job payload, with its final progress.
        """

        page_id = payload.get("progress", {}).get("page_id")
        if page_id:
            db.finish_document(page_id, payload["fingerprint"])
        self.discard_pdf(payload)

    def discard_pdf(self, payload: Dict[str, Any]) -> None:
        """Delete a job's parked file (it is no longer needed)."""

        Path(payload["pdf_path"]).unlink(missing_ok=True)

    def _parse_visit(self, visit: Dict[str, Any]) -> Dict[str, Any]:
        """Turn the visit's ISO timestamp (JSON payload) back into a datetime."""

        parsed = dict(visit)
        parsed["started_at"] = datetime.fromisoformat(parsed["started_at"])
        return parsed
