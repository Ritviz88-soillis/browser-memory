"""Retrieval stage — hybrid search over the indexed chunks.

Vector and keyword rankings are fused with Reciprocal Rank Fusion in
``db.hybrid_search``. This service resolves the filters and embeds the query
around that call.
"""

from typing import Any, Dict, List, Optional, Tuple

import config
import db
from services.embedding_service import get_embedder
from services.query_filter_service import ParsedQuery


class RetrievalService:
    """Finds the chunks most relevant to a parsed question."""

    def __init__(self) -> None:
        """Remember which embedding model the query side uses."""

        self.embedding_model = config.EMBEDDING_MODEL

    async def retrieve(
        self,
        parsed: ParsedQuery,
        top: int = config.RETRIEVAL_TOP_K,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Run filtered hybrid search for a parsed question.

        Args:
            parsed: The topical query and its date/site filters.
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

        embedder = get_embedder(self.embedding_model)
        query_vector = await embedder.embed_query(parsed.semantic_query)

        rows = db.hybrid_search(
            query_vector,
            parsed.semantic_query,
            since=parsed.since,
            until=parsed.until,
            domains=domains,
            limit=top,
        )
        return rows, None
