"""Retrieval stage — hybrid search over the indexed chunks.

Vector and keyword rankings are fused with Reciprocal Rank Fusion in
``db.hybrid_search``. This service resolves the site filter around that call.
It does not embed anything: the caller passes the question's vector in.
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import config
import db
from schemas import ParsedQuery


class RetrievalService:
    """Finds the chunks in memory most relevant to a parsed question."""

    def search(
        self,
        parsed: ParsedQuery,
        query_vector: Sequence[float],
        top: int = config.RETRIEVAL_TOP_K,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Run filtered hybrid search for a parsed question.

        Args:
            parsed: The topical query and its date/site filters.
            query_vector: The embedding of the topical query.
            top: How many chunks to return.

        Returns:
            The matching chunks (best first) and, when the question names a
            site the user never visited, the reason to abstain instead.
        """

        domains = None
        if parsed.domains:
            domains = db.known_domains(parsed.domains)
            # a mentioned-but-never-visited site must abstain, not search everywhere
            if not domains:
                return [], f"You haven't read anything on {', '.join(parsed.domains)}."

        rows = db.hybrid_search(
            query_vector,
            parsed.semantic_query,
            since=parsed.since,
            until=parsed.until,
            domains=domains,
            limit=top,
        )
        return rows, None

    def without_pages(
        self,
        rows: List[Dict[str, Any]],
        urls: Set[str],
    ) -> List[Dict[str, Any]]:
        """Drop chunks belonging to the given pages.

        Args:
            rows: Chunks returned by ``search``.
            urls: Normalized URLs of pages already supplied another way
                (the open tab is passed live, so its stored copy is redundant).

        Returns:
            The rows from every other page.
        """

        return [row for row in rows if row["url"] not in urls]

    def similar_enough(
        self,
        rows: List[Dict[str, Any]],
        min_similarity: float,
    ) -> List[Dict[str, Any]]:
        """Keep only chunks that are genuinely close to the question.

        Search always returns its best matches, even when memory holds
        nothing on the topic. Beside an open page, those leftovers only
        distract, so they are dropped.

        Args:
            rows: Chunks returned by ``search`` (each carries ``similarity``).
            min_similarity: The cosine similarity a chunk must reach.

        Returns:
            The rows at or above the floor, order unchanged.
        """

        return [row for row in rows if row["similarity"] >= min_similarity]
