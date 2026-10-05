"""Proactive recall: surface pages the user read before that relate to the
page open now — without being asked.

Deliberately uses no LLM. This service describes the open page as one short
text and looks up similar pages; the orchestrator embeds the description in
between.
"""

from typing import Optional, Sequence

import config
import db
from schemas import CurrentPageIn, RelatedOut, RelatedPageOut


class RecallService:
    """Finds previously read pages similar to the current page."""

    def describe(
        self,
        page: CurrentPageIn,
        transcript: Optional[str] = None,
        stored_text: Optional[str] = None,
    ) -> str:
        """Build the text that represents the open page for similarity search.

        Args:
            page: The open tab as sent by the extension.
            transcript: The video transcript, if the page is a video.
            stored_text: The page's text from memory, if extraction failed.

        Returns:
            Title plus the opening of the page text; empty if neither exists.
        """

        text = transcript or page.text or stored_text or ""
        parts = [page.title or "", text[: config.RELATED_QUERY_CHARS]]
        return "\n\n".join(part for part in parts if part.strip())

    def find(self, page_vector: Sequence[float], exclude_url: str) -> RelatedOut:
        """Look up pages in memory close to the open page.

        Args:
            page_vector: The embedding of the page's description.
            exclude_url: The open page's normalized URL (never related to itself).

        Returns:
            Up to ``config.RELATED_MAX_PAGES`` pages above the similarity
            cutoff, most similar first. Empty when nothing is close enough.
        """

        rows = db.related_pages(
            page_vector,
            exclude_url=exclude_url,
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
