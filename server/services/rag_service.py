"""The RAG brain: sequences filter extraction -> retrieval -> generation.

Holds no algorithm itself (mirrors t1's GaugeReadingService) — it wires the
stage services together, numbers the sources, and applies the answering
policy (when to abstain, what gets logged).
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
import db
from schemas import AskIn, AskOut, CurrentPageIn, FilterOut, SourceOut
from services.generation_service import GenerationService
from services.live_page_service import LivePageService
from services.query_filter_service import ParsedQuery, QueryFilterService
from services.retrieval_service import RetrievalService
from utils.citations import validate_citations
from utils.formatting import Source, format_sources, live_page_sources, rows_to_sources
from utils.llm import chat_model_name
from utils.urls import normalize_url


class RAGService:
    """Answers a question from the user's browsing memory, with citations."""

    def __init__(self) -> None:
        """Instantiate the stage services once (reused across questions)."""

        self._query_filter = QueryFilterService()
        self._retrieval = RetrievalService()
        self._live_pages = LivePageService()
        self._generation = GenerationService()

    async def answer(self, request: AskIn, device_id: Optional[str]) -> AskOut:
        """Answer a question grounded in indexed pages and the open tab.

        Args:
            request: The question, conversation history and current page.
            device_id: The asking device, recorded with the logged query.

        Returns:
            The answer, the passages it cites, and the filters that were applied.
        """

        started = time.monotonic()

        # 1. Extract date/site filters from the question
        if request.no_filters:
            parsed = ParsedQuery(semantic_query=request.question)
        else:
            parsed = await self._query_filter.parse(request.question)

        # 2. Pick the relevant passages of the page open right now
        live_pages = [request.current_page] if request.current_page else []
        sources = await self._live_sources(live_pages, parsed.semantic_query)

        # 3. Retrieve matching chunks from memory (the open page's own copy in
        #    memory is dropped: its live passages already cover it)
        rows, abstain_reason = await self._retrieval.retrieve(parsed, request.top)
        rows = self._without_pages(rows, live_pages)
        sources += rows_to_sources(rows, start=len(sources) + 1)

        # 4. Generate, then keep only citations of sources the model was shown
        cited: List[Source] = []
        if not sources:
            answer = abstain_reason or config.ABSTAIN_TEXT
            abstained = True
        else:
            raw_answer = await self._generation.generate(
                question=request.question,
                context=format_sources(sources),
                history=request.history,
            )
            answer, cited = validate_citations(raw_answer, sources)
            abstained = False

        latency_ms = int((time.monotonic() - started) * 1000)

        # 5. Log the exchange for the evaluation harness
        db.log_query(
            device_id=device_id,
            question=request.question,
            parsed_filter=parsed.model_dump(mode="json"),
            chunk_ids=[row["id"] for row in rows],
            answer=answer,
            abstained=abstained,
            embed_model=self._retrieval.embedding_model,
            llm_model=chat_model_name(),
            latency_ms=latency_ms,
        )

        return AskOut(
            answer=answer,
            abstained=abstained,
            sources=[self._to_source_out(source) for source in cited],
            filters=FilterOut(**parsed.model_dump()),
            latency_ms=latency_ms,
        )

    async def _live_sources(
        self,
        pages: List[CurrentPageIn],
        question: str,
    ) -> List[Source]:
        """Build numbered sources from the passages of the open pages.

        Args:
            pages: The open tabs sent with the question.
            question: The topical part of the user's question.

        Returns:
            Live sources numbered from 1, page by page.
        """

        now = datetime.now(timezone.utc)
        sources: List[Source] = []
        for page in pages:
            passages = await self._live_pages.passages(page, question)
            sources += live_page_sources(
                page.url,
                page.title,
                page.tab_id,
                passages,
                now,
                start=len(sources) + 1,
            )
        return sources

    def _without_pages(
        self,
        rows: List[Dict[str, Any]],
        pages: List[CurrentPageIn],
    ) -> List[Dict[str, Any]]:
        """Drop memory rows that belong to a page already supplied live."""

        live_urls = {normalize_url(page.url)[0] for page in pages}
        return [row for row in rows if row["url"] not in live_urls]

    def _to_source_out(self, source: Source) -> SourceOut:
        """Shape a cited source for the API response."""

        return SourceOut(
            n=source.n,
            title=source.title,
            url=source.url,
            domain=source.domain,
            heading_path=source.heading_path,
            visited=source.visited,
            snippet=source.text[: config.SOURCE_SNIPPET_CHARS],
            passage=source.text,
            live=source.live,
            tab_id=source.tab_id,
        )
