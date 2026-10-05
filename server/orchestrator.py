"""The orchestrator: every flow of the server, written as a sequence of steps.

This is the ONLY file that calls services. Each service does one job and knows
nothing about the others; when one stage needs another's output (retrieval
needs the question's embedding, storage needs the chunks), the orchestrator
fetches it and passes it along. To understand what happens when a page is
indexed or a question is asked, read the matching method here top to bottom.

    ingest       queue a page the extension sent
    process_job  index a queued page:   scrub -> chunk -> embed -> store
    ask          answer a question:     filter -> embed -> open-page passages
                                        -> memory search -> generate -> cite
    related      proactive recall:      describe page -> embed -> find similar
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import config
import worker
from schemas import (
    AskIn,
    AskOut,
    CurrentPageIn,
    FilterOut,
    IngestIn,
    IngestOut,
    PageOut,
    ParsedQuery,
    RelatedOut,
    SourceOut,
    StatusOut,
)
from services.chunking_service import ChunkingService
from services.embedding_service import EmbeddingService
from services.generation_service import GenerationService
from services.ingestion_service import IngestionService
from services.live_page_service import LivePageService
from services.page_service import PageService
from services.query_filter_service import QueryFilterService
from services.query_log_service import QueryLogService
from services.recall_service import RecallService
from services.retrieval_service import RetrievalService
from services.transcript_service import TranscriptService
from utils import youtube
from utils.citations import validate_citations
from utils.formatting import Source, format_sources, live_page_sources, rows_to_sources
from utils.llm import chat_model_name
from utils.scrub import scrub
from utils.urls import normalize_url

logger = logging.getLogger(__name__)


class MemoryOrchestrator:
    """Runs each request through the services it needs, in order."""

    def __init__(self) -> None:
        """Create every service once (reused across requests)."""

        self._chunking = ChunkingService()
        self._embedding = EmbeddingService()
        self._transcripts = TranscriptService()
        self._ingestion = IngestionService()
        self._query_filter = QueryFilterService()
        self._retrieval = RetrievalService()
        self._live_pages = LivePageService()
        self._generation = GenerationService()
        self._query_log = QueryLogService()
        self._recall = RecallService()
        self._pages = PageService()

    # --- indexing ----------------------------------------------------------

    async def ingest(self, request: IngestIn, device_id: str) -> IngestOut:
        """Queue a page for background indexing; returns immediately.

        Args:
            request: The extracted page and its visit details.
            device_id: The device that read the page.

        Returns:
            Whether the page was queued or was a duplicate.
        """

        logger.info("queueing '%s'", request.url)
        result = self._ingestion.enqueue(request, device_id)
        worker.notify()
        return result

    async def process_job(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Index one queued page (called by the background worker).

        Args:
            payload: The job payload written at ingest time.

        Returns:
            The page id and whether new content was indexed, or None when the
            page was skipped (blocked site, or nothing readable).
        """

        url, domain = normalize_url(payload["url"])

        # 1. Skip sites the user asked to forget
        if self._ingestion.is_blocked(domain):
            return None

        # 2. Redact secrets BEFORE chunking: the chunker owns the canonical
        #    text, so redacting afterwards would desynchronize chunk offsets
        scrubbed = scrub(payload["html"])

        # 3. Split into heading-aware chunks
        extracted_text, chunks = self._chunking.chunk(scrubbed.text)
        if not chunks:
            return None

        # 4. Unchanged page seen again: record the visit, skip the embedding
        page_id = self._ingestion.record_revisit(url, extracted_text, payload)
        if page_id:
            return {"page_id": page_id, "new_content": False}

        # 5. Embed every chunk of the page in one batch
        vectors = await self._embedding.embed_passages([chunk.text for chunk in chunks])

        # 6. Store page, chunks and visit in one transaction
        page_id = self._ingestion.store(payload, url, domain, extracted_text, chunks, vectors)
        return {"page_id": page_id, "new_content": True}

    # --- asking ------------------------------------------------------------

    async def ask(self, request: AskIn, device_id: Optional[str]) -> AskOut:
        """Answer a question from browsing memory and the open tabs.

        Args:
            request: The question, conversation history, the current page and
                any tabs ticked for comparison.
            device_id: The asking device, recorded with the logged query.

        Returns:
            The answer, the passages it cites, and the filters that were applied.
        """

        started = time.monotonic()
        comparing_tabs = bool(request.tabs)

        # 1. Extract date/site filters from the question (not needed when the
        #    user has ticked the tabs to answer from)
        if request.no_filters or comparing_tabs:
            parsed = ParsedQuery(semantic_query=request.question)
        else:
            parsed = await self._query_filter.parse(request.question)

        # 2. Embed the question once; both passage selection and memory
        #    search compare against this vector
        query_vector = await self._embedding.embed_query(parsed.semantic_query)

        # 3. Pick the relevant passages of the open pages: the ticked tabs,
        #    or else the page open right now. These are numbered first.
        if comparing_tabs:
            live_pages = request.tabs
            budget_per_page = config.COMPARE_TABS_CHAR_BUDGET // len(live_pages)
        else:
            live_pages = [request.current_page] if request.current_page else []
            budget_per_page = config.LIVE_PAGE_CHAR_BUDGET

        now = datetime.now(timezone.utc)
        sources: List[Source] = []
        for page in live_pages:
            passages = await self._open_page_passages(page, query_vector, budget_per_page)
            sources += live_page_sources(
                page.url, page.title, page.tab_id, passages, now, start=len(sources) + 1
            )

        # 4. Search memory — unless tabs were ticked, which means "answer from
        #    these". The open page's own copy in memory is dropped: its live
        #    passages already cover it.
        rows: List[Dict[str, Any]] = []
        abstain_reason: Optional[str] = None
        if not comparing_tabs:
            rows, abstain_reason = self._retrieval.search(parsed, query_vector, request.top)
            open_urls = {normalize_url(page.url)[0] for page in live_pages}
            rows = self._retrieval.without_pages(rows, open_urls)
            sources += rows_to_sources(rows, start=len(sources) + 1)

        # 5. Generate the answer, or abstain when there is nothing to ground it
        cited: List[Source] = []
        if not sources:
            if comparing_tabs:
                answer = config.UNREADABLE_TABS_TEXT
            else:
                answer = abstain_reason or config.ABSTAIN_TEXT
            abstained = True
        else:
            raw_answer = await self._generation.generate(
                question=request.question,
                context=format_sources(sources),
                history=request.history,
            )
            # 6. Keep only citations of sources the model was actually shown
            answer, cited = validate_citations(raw_answer, sources)
            abstained = False

        latency_ms = int((time.monotonic() - started) * 1000)

        # 7. Log the exchange for the evaluation harness
        self._query_log.record(
            device_id=device_id,
            question=request.question,
            parsed_filter=parsed.model_dump(mode="json"),
            chunk_ids=[row["id"] for row in rows],
            answer=answer,
            abstained=abstained,
            embed_model=self._embedding.model_id,
            llm_model=chat_model_name(),
            latency_ms=latency_ms,
        )

        return AskOut(
            answer=answer,
            abstained=abstained,
            sources=[self._source_out(source) for source in cited],
            filters=FilterOut(**parsed.model_dump()),
            latency_ms=latency_ms,
        )

    async def _open_page_passages(
        self,
        page: CurrentPageIn,
        query_vector: Sequence[float],
        char_budget: int,
    ) -> List[Any]:
        """Turn one open page into the passages relevant to the question.

        Args:
            page: The open tab as sent by the extension.
            query_vector: The embedding of the question.
            char_budget: Maximum total characters of passage text from this page.

        Returns:
            The selected chunks in page order; empty if nothing is readable.
        """

        # a. Choose the page's content: a video's transcript, else what the
        #    extension extracted, else the copy already in memory
        transcript = await self._transcript_for(page.url)
        stored_text = None
        if not (transcript or page.html or page.text):
            stored_text = self._pages.stored_text(normalize_url(page.url)[0])

        article_html = self._live_pages.article_html(page, transcript, stored_text)
        if not article_html:
            return []

        # b. Chunk and embed it — once per page; follow-up questions reuse it
        cached = self._live_pages.cached_passages(page.url, article_html)
        if cached is None:
            _, chunks = self._chunking.chunk(scrub(article_html).text)
            vectors = await self._embedding.embed_passages([chunk.text for chunk in chunks])
            cached = self._live_pages.cache_passages(page.url, article_html, chunks, vectors)
        chunks, matrix = cached

        # c. Keep the passages closest to the question, within the budget
        return self._live_pages.select(chunks, matrix, query_vector, char_budget)

    async def _transcript_for(self, url: str) -> Optional[str]:
        """Fetch the transcript when the URL is a YouTube video, else None."""

        video_id = youtube.video_id(url)
        if not video_id:
            return None
        return await self._transcripts.fetch_transcript(video_id)

    def _source_out(self, source: Source) -> SourceOut:
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

    # --- proactive recall --------------------------------------------------

    async def related(self, page: CurrentPageIn) -> RelatedOut:
        """Find previously read pages related to the page open now (no LLM).

        Args:
            page: The open tab: its URL, title and readable text.

        Returns:
            Related pages above the similarity cutoff, most similar first.
        """

        normalized_url, _ = normalize_url(page.url)

        # 1. Describe the open page in one short text
        transcript = await self._transcript_for(page.url)
        stored_text = None
        if not (transcript or page.text):
            stored_text = self._pages.stored_text(normalized_url)

        description = self._recall.describe(page, transcript, stored_text)
        if not description:
            return RelatedOut(pages=[])

        # 2. Embed it as a passage: this compares content with content, not a
        #    question with content
        vectors = await self._embedding.embed_passages([description])

        # 3. Nearest pages in memory, excluding the page itself
        return self._recall.find(vectors[0], normalized_url)

    # --- pages panel -------------------------------------------------------

    async def health_check(self) -> Dict[str, bool]:
        """Module health status (verifies the database is usable)."""

        self._pages.ping()
        return {"ok": True}

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
