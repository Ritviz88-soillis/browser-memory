"""The orchestrator: every flow of the server, written as a sequence of steps.

This is the ONLY file that calls services. Each service does one job and knows
nothing about the others; when one stage needs another's output (retrieval
needs the question's embedding, storage needs the chunks), the orchestrator
fetches it and passes it along. To understand what happens when a page is
indexed or a question is asked, read the matching method here top to bottom.

    ingest       queue a page the extension sent
    process_job  index a queued page:   scrub -> chunk -> embed -> store
    ask          answer a question:     understand question + prepare open pages
                                        -> embed -> select passages -> memory
                                        search -> generate -> validate citations
                                        -> pick supporting sentences
    related      proactive recall:      describe page -> embed -> find similar
"""

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config
import worker
from prompts.answer_prompt import NOT_FOUND
from schemas import (
    AskIn,
    AskOut,
    CurrentPageIn,
    FilterOut,
    IngestIn,
    IngestOut,
    IngestPdfIn,
    PageOut,
    PairOut,
    ParsedQuery,
    RelatedOut,
    SourceOut,
    StatusOut,
    TrustOut,
)
from services.chunking_service import ChunkingService
from services.device_service import DeviceService
from services.embedding_service import EmbeddingService
from services.evidence_service import EvidenceService
from services.generation_service import GenerationService
from services.ingestion_service import IngestionService
from services.live_page_service import LivePageService
from services.page_service import PageService
from services.pdf_service import PdfService
from services.query_filter_service import QueryFilterService
from services.query_log_service import QueryLogService
from services.recall_service import RecallService
from services.retrieval_service import RetrievalService
from services.transcript_service import TranscriptService
from utils import youtube
from utils.citations import validate_citations
from utils.formatting import Source, format_sources, live_page_sources, rows_to_sources
from utils.llm import LLMUnavailable, chat_model_name, describe_llm_error
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
        self._evidence = EvidenceService()
        self._query_log = QueryLogService()
        self._recall = RecallService()
        self._pages = PageService()
        self._pdf = PdfService()
        self._devices = DeviceService()

    async def warm_up(self) -> None:
        """Run the embedding model once at startup.

        Its first call is several seconds slower than the rest; paying that
        here means the first page the user reads is indexed as fast as any.
        """

        await self._embedding.embed_passages(["warm up"])

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

    async def ingest_pdf(self, request: IngestPdfIn, device_id: str) -> IngestOut:
        """Queue a PDF the user is reading for background indexing.

        Args:
            request: The PDF file (base64) and its visit details.
            device_id: The device that read it.

        Returns:
            Whether the PDF was queued or was a duplicate.

        Raises:
            ValueError: If what was sent is not a PDF file.
        """

        # 1. Decode the file and check it is a PDF at all
        try:
            data = base64.b64decode(request.pdf_base64, validate=True)
        except ValueError as error:
            raise ValueError("the PDF was not sent in a readable form") from error
        if not self._pdf.looks_like_pdf(data):
            raise ValueError("this file could not be read as a PDF")

        # 2. Park it on disk and queue a job pointing at it. Nothing is read
        #    here: a long PDF is worked through in batches by the worker.
        logger.info("queueing PDF '%s' (%d KB)", request.url, len(data) // 1024)
        result = self._ingestion.enqueue_pdf(request, data, device_id)
        worker.notify()
        return result

    async def process_job(
        self,
        payload: Dict[str, Any],
        job_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Index one queued page or PDF (called by the background worker).

        Args:
            payload: The job payload written at ingest time.
            job_id: The queue job, so a long document can save its progress.

        Returns:
            The page id and whether new content was indexed, or None when the
            page was skipped (blocked site, or nothing readable).
        """

        if payload.get("kind") == "pdf":
            return await self._process_pdf(payload, job_id)

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

    async def _process_pdf(
        self,
        payload: Dict[str, Any],
        job_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Index a parked PDF a few pages at a time.

        Each batch is read, chunked, embedded and saved before the next one
        starts. So the first pages are searchable within a second or two, a
        question asked meanwhile is answered between batches, and if the
        server stops, the job resumes after the last batch that was saved.

        Args:
            payload: The job payload; ``progress`` is present when resuming.
            job_id: The queue job to record progress on.

        Returns:
            The page id and whether new content was indexed, or None when the
            PDF was skipped (blocked site, unreadable, or no text).
        """

        url, domain = normalize_url(payload["url"])
        path = payload["pdf_path"]

        # 1. Skip sites the user asked to forget
        if self._ingestion.is_blocked(domain):
            self._ingestion.discard_pdf(payload)
            return None

        # 2. A fresh job (not one being resumed): count the pages, and stop
        #    here if this exact file is already in memory
        if "progress" not in payload:
            try:
                total_pages = await asyncio.to_thread(self._pdf.page_count, path)
            except ValueError as error:
                logger.warning("skipping PDF '%s': %s", payload["url"], error)
                self._ingestion.discard_pdf(payload)
                return None

            page_id = self._ingestion.record_pdf_revisit(url, payload)
            if page_id:
                self._ingestion.discard_pdf(payload)
                return {"page_id": page_id, "new_content": False}

            payload["title"] = await asyncio.to_thread(self._pdf.title, path) or payload.get("title")
            payload["progress"] = {
                "pages_done": 0,
                "total_pages": total_pages,
                "page_id": None,
                "chunks": 0,
                "text_chars": 0,
                "furniture": None,
            }

        # 3. Work through the remaining pages, one batch at a time
        progress = payload["progress"]
        while progress["pages_done"] < progress["total_pages"]:
            start = progress["pages_done"]
            stop = min(start + config.PDF_PAGES_PER_BATCH, progress["total_pages"])

            # a. Read this batch's pages; the first batch also learns which
            #    lines are running headers and footers
            pages = await asyncio.to_thread(self._pdf.read_pages, path, start, stop)
            if progress["furniture"] is None:
                progress["furniture"] = self._pdf.furniture(pages)
            article_html = self._pdf.pages_html(
                pages, start + 1, progress["furniture"], payload.get("title")
            )

            # b. Scrub secrets, then split into passages (each keeps its
            #    "Page N" heading; the bare title heading is not a passage)
            extracted_text, chunks = "", []
            if article_html:
                extracted_text, chunks = self._chunking.chunk(scrub(article_html).text)
                chunks = [chunk for chunk in chunks if chunk.text.strip() != (payload.get("title") or "")]

            # c. Embed the batch
            vectors = await self._embedding.embed_passages([chunk.text for chunk in chunks])

            # d. Save the batch and the progress together
            progress = self._ingestion.store_pdf_batch(
                job_id, payload, url, domain, extracted_text, chunks, vectors, pages_done=stop
            )
            logger.info("PDF '%s': %d of %d pages", payload["url"], stop, progress["total_pages"])

            # e. Let anything that was waiting (a question, say) run
            await asyncio.sleep(0)

        # 4. Done: record the file's fingerprint and remove the parked file
        self._ingestion.finish_pdf(payload)
        if progress["page_id"] is None:
            logger.warning("PDF '%s' has no readable text (scanned?)", payload["url"])
            return None
        return {"page_id": progress["page_id"], "new_content": True}

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

        # The open pages to answer from: the ticked tabs, or else the page
        # open right now. Ticked tabs share one budget equally.
        if comparing_tabs:
            live_pages = request.tabs
            budget_per_page = config.COMPARE_TABS_CHAR_BUDGET // len(live_pages)
        else:
            live_pages = [request.current_page] if request.current_page else []
            budget_per_page = config.LIVE_PAGE_CHAR_BUDGET

        # 1. Two things that do not depend on each other run at the same time:
        #    understanding the question (a call to the language model) and
        #    chunking + embedding the open pages (local work).
        parsed, *prepared_pages = await asyncio.gather(
            self._understand_question(request, comparing_tabs),
            *(self._prepare_open_page(page) for page in live_pages),
        )

        # 2. Embed the question once; both passage selection and memory
        #    search compare against this vector
        query_vector = await self._embedding.embed_query(parsed.semantic_query)

        # 3. Keep the passages of each open page closest to the question.
        #    These sources are numbered first.
        now = datetime.now(timezone.utc)
        sources: List[Source] = []
        for page, prepared in zip(live_pages, prepared_pages):
            if prepared is None:
                continue
            chunks, matrix = prepared
            passages = self._live_pages.select(chunks, matrix, query_vector, budget_per_page)
            sources += live_page_sources(
                page.url, page.title, page.tab_id, passages, now, start=len(sources) + 1
            )
        has_open_page = bool(sources)

        # 4. Search memory — unless tabs were ticked, which means "answer from
        #    these". The open page's own copy in memory is dropped: its live
        #    passages already cover it.
        rows: List[Dict[str, Any]] = []
        abstain_reason: Optional[str] = None
        if not comparing_tabs:
            rows, abstain_reason = self._retrieval.search(parsed, query_vector, request.top)
            open_urls = {normalize_url(page.url)[0] for page in live_pages}
            rows = self._retrieval.without_pages(rows, open_urls)
            if has_open_page:
                # the page is the subject; memory is added only when it is
                # clearly about the same thing
                rows = self._retrieval.similar_enough(
                    rows, config.MEMORY_MIN_SIMILARITY_BESIDE_OPEN_PAGE
                )
            sources += rows_to_sources(rows, start=len(sources) + 1)

        # 5. Generate the answer, or abstain when there is nothing to ground it
        cited: List[Source] = []
        highlights: Dict[int, List[str]] = {}
        trust: Optional[TrustOut] = None
        if not sources:
            if comparing_tabs:
                answer = config.UNREADABLE_TABS_TEXT
            else:
                answer = abstain_reason or config.ABSTAIN_TEXT
            abstained = True
        else:
            try:
                raw_answer = await self._generation.generate(
                    question=request.question,
                    context=format_sources(sources),
                    history=request.history,
                )
            except Exception as error:
                # a used-up free quota or a dropped connection, not a bug:
                # say so plainly instead of failing with a raw error
                logger.warning("generation failed: %s", str(error)[:200])
                raise LLMUnavailable(describe_llm_error(error)) from error

            if self._says_not_found(raw_answer):
                # the sources were read and hold no answer: say so in words
                # that fit where the user was looking
                if comparing_tabs:
                    answer = config.TABS_NOT_COVERED_TEXT
                elif has_open_page:
                    answer = config.PAGE_NOT_COVERED_TEXT
                else:
                    answer = config.ABSTAIN_TEXT
                abstained = True
            else:
                # 6. Keep only citations of sources the model was actually shown
                answer, cited = validate_citations(raw_answer, sources)
                # 7. Check each cited sentence against the passage it cites,
                #    and withdraw the citation where the passage does not
                #    support it (the model stating what it already knew).
                #    Sources left with no supported sentence drop out.
                answer = self._evidence.mark_unsupported(
                    answer, {source.n: source.text for source in cited}
                )
                answer, cited = validate_citations(answer, sources)
                trust = TrustOut(**self._evidence.tally(answer))
                abstained = False
                # 8. Narrow each cited passage to the sentences that support
                #    the answer; these are what the page highlights
                highlights = await self._supporting_sentences(answer, cited)

        latency_ms = int((time.monotonic() - started) * 1000)

        # 9. Log the exchange for the evaluation harness
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
            trust=trust,
            sources=[
                self._source_out(source, highlights.get(source.n, [])) for source in cited
            ],
            filters=FilterOut(**parsed.model_dump()),
            latency_ms=latency_ms,
        )

    async def retrieve(
        self,
        question: str,
        top: int = config.RETRIEVAL_TOP_K,
        use_vectors: bool = True,
        use_keywords: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search memory for a question, without generating an answer.

        No language model is involved: the question is embedded locally and
        searched as it stands. The evaluation uses this to compare vector,
        keyword and combined search.

        Args:
            question: The question, used directly as the search query.
            top: How many chunks to return.
            use_vectors: Include the vector ranking.
            use_keywords: Include the keyword ranking.

        Returns:
            The matching chunks with their page details, best first.
        """

        query_vector = await self._embedding.embed_query(question)
        rows, _ = self._retrieval.search(
            ParsedQuery(semantic_query=question), query_vector, top, use_vectors, use_keywords
        )
        return rows

    async def _understand_question(self, request: AskIn, comparing_tabs: bool) -> ParsedQuery:
        """Work out what to search for, and any date or site filters.

        Args:
            request: The question and the conversation so far.
            comparing_tabs: Whether the user ticked the tabs to answer from
                (then there is nothing to filter, and the question is used as is).

        Returns:
            The topical query and its filters.
        """

        if request.no_filters or comparing_tabs:
            return ParsedQuery(semantic_query=request.question)
        return await self._query_filter.parse(request.question, request.history)

    async def _prepare_open_page(self, page: CurrentPageIn) -> Optional[Tuple[List[Any], Any]]:
        """Split one open page into passages and embed them.

        Args:
            page: The open tab as sent by the extension.

        Returns:
            The page's chunks and their embedding matrix, or None if the page
            has nothing readable.
        """

        # a. Choose the page's content: a video's transcript, else what the
        #    extension extracted
        transcript = await self._transcript_for(page.url)
        article_html = self._live_pages.article_html(page, transcript)

        if not article_html:
            # The tab could not be read live (a PDF in the browser's viewer,
            # an orphaned content script). If the page is in memory, use its
            # stored passages as they are: they keep their headings, such as
            # the PDF page number, and are already embedded.
            stored = self._pages.stored_passages(normalize_url(page.url)[0])
            if stored is None:
                return None
            fingerprint, passages, vectors = stored
            return self._live_pages.cached_passages(
                page.url, fingerprint
            ) or self._live_pages.cache_passages(page.url, fingerprint, passages, vectors)

        # b. Chunk and embed it — once per page; follow-up questions reuse it
        cached = self._live_pages.cached_passages(page.url, article_html)
        if cached is None:
            _, chunks = self._chunking.chunk(scrub(article_html).text)
            vectors = await self._embedding.embed_passages([chunk.text for chunk in chunks])
            cached = self._live_pages.cache_passages(page.url, article_html, chunks, vectors)
        return cached

    def _says_not_found(self, raw_answer: str) -> bool:
        """Whether the model reported that the sources hold no answer."""

        return raw_answer.strip(" \n\t`*\"'.").upper().startswith(NOT_FOUND)

    async def _supporting_sentences(
        self,
        answer: str,
        cited: List[Source],
    ) -> Dict[int, List[str]]:
        """For each cited passage, find the sentences that back up the answer.

        Args:
            answer: The validated answer, with [n] citations.
            cited: The sources the answer cites.

        Returns:
            Source number -> exact sentences of its passage to highlight.
        """

        # a. Which answer sentences cite which source, and each cited
        #    passage split into sentences
        claims = self._evidence.claims(answer)
        spans = {
            source.n: self._evidence.spans(source.text)
            for source in cited
            if claims.get(source.n)
        }
        spans = {number: found for number, found in spans.items() if found}
        if not spans:
            return {}

        # b. Embed all of them in one local batch
        texts: List[str] = []
        for number, found in spans.items():
            texts += found + claims[number]
        vectors = await self._embedding.embed_passages(texts)

        # c. Per source, keep the sentences closest to the claims citing it
        highlights: Dict[int, List[str]] = {}
        position = 0
        for number, found in spans.items():
            span_vectors = vectors[position : position + len(found)]
            position += len(found)
            claim_vectors = vectors[position : position + len(claims[number])]
            position += len(claims[number])
            highlights[number] = self._evidence.select(found, span_vectors, claim_vectors)
        return highlights

    async def _transcript_for(self, url: str) -> Optional[str]:
        """Fetch the transcript when the URL is a YouTube video, else None."""

        video_id = youtube.video_id(url)
        if not video_id:
            return None
        return await self._transcripts.fetch_transcript(video_id)

    def _source_out(self, source: Source, highlights: List[str]) -> SourceOut:
        """Shape a cited source for the API response."""

        return SourceOut(
            highlights=highlights,
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

    # --- pairing -----------------------------------------------------------

    async def pair(self, origin: Optional[str]) -> PairOut:
        """Give the browser extension its access token.

        Args:
            origin: The request's Origin header, which identifies the caller.

        Returns:
            A new token for the extension to send with every later request.

        Raises:
            PermissionError: If the caller is not the trusted extension.
        """

        return PairOut(token=self._devices.pair(origin))

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
