"""Proactive recall: surface pages the user read before that relate to the
page open now — without being asked.

Deliberately uses no LLM: one local embedding plus one database search, so it
is free to run on every page the user opens.
"""

from typing import Optional

import config
import db
from schemas import CurrentPageIn, RelatedOut, RelatedPageOut
from services.embedding_service import get_embedder
from services.transcript_service import TranscriptService
from utils import youtube
from utils.urls import normalize_url


class RecallService:
    """Finds previously read pages similar to the current page."""

    def __init__(self, transcripts: Optional[TranscriptService] = None) -> None:
        """Set up the embedding model name and the transcript source.

        Args:
            transcripts: Transcript service to share (keeps one cache per app).
        """

        self._embedding_model = config.EMBEDDING_MODEL
        self._transcripts = transcripts or TranscriptService()

    async def related(self, page: CurrentPageIn) -> RelatedOut:
        """Find pages in memory related to the page open now.

        Args:
            page: The open tab: its URL, title and readable text.

        Returns:
            Up to ``config.RELATED_MAX_PAGES`` pages above the similarity
            cutoff, most similar first. Empty when nothing is close enough.
        """

        normalized_url, _ = normalize_url(page.url)

        # 1. Describe the current page in one short text
        query_text = await self._describe_page(page, normalized_url)
        if not query_text:
            return RelatedOut(pages=[])

        # 2. Embed it as a passage: this compares content with content, not a
        #    question with content
        embedder = get_embedder(self._embedding_model)
        vectors = await embedder.embed_passages([query_text])

        # 3. Nearest pages in memory, excluding the page itself
        rows = db.related_pages(
            vectors[0],
            exclude_url=normalized_url,
            min_similarity=config.RELATED_MIN_SIMILARITY,
            limit=config.RELATED_MAX_PAGES,
            candidate_chunks=config.RELATED_CANDIDATE_CHUNKS,
        )

        return RelatedOut(
            pages=[
                RelatedPageOut(
                    id=row["page_id"],
                    title=row["title"] or row["domain"],
                    url=row["url"],
                    domain=row["domain"],
                    visited=row["last_visited_at"],
                    snippet=row["text"][: config.RELATED_SNIPPET_CHARS],
                    similarity=round(float(row["similarity"]), 3),
                )
                for row in rows
            ]
        )

    async def _describe_page(self, page: CurrentPageIn, normalized_url: str) -> str:
        """Build the text that represents the current page for similarity search.

        Args:
            page: The open tab as sent by the extension.
            normalized_url: The page URL in its stored (normalized) form.

        Returns:
            Title plus the opening of the page text; empty if neither exists.
        """

        text = page.text

        # YouTube: the transcript is the content, the page text is UI chrome
        video_id = youtube.video_id(page.url)
        if video_id:
            text = await self._transcripts.fetch_transcript(video_id) or text

        # extraction failed, but the page may already be in memory
        if not text:
            text = db.page_text_for_url(normalized_url)

        parts = [page.title or "", (text or "")[: config.RELATED_QUERY_CHARS]]
        return "\n\n".join(part for part in parts if part.strip())
