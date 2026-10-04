"""The RAG brain: sequences filter extraction -> retrieval -> generation.

Holds no algorithm itself (mirrors t1's GaugeReadingService) — it wires the
stage services together, resolves the current-page source, and applies the
answering policy (when to abstain, what gets logged).
"""

import time
from datetime import datetime, timezone
from typing import List, Optional

import config
import db
from schemas import AskIn, AskOut, CurrentPageIn, FilterOut, SourceOut
from services.generation_service import GenerationService
from services.query_filter_service import ParsedQuery, QueryFilterService
from services.retrieval_service import RetrievalService
from services.transcript_service import TranscriptService
from utils import youtube
from utils.citations import validate_citations
from utils.formatting import Source, current_page_source, format_sources, rows_to_sources
from utils.llm import chat_model_name
from utils.urls import normalize_url


class RAGService:
    """Answers a question from the user's browsing memory, with citations."""

    def __init__(self) -> None:
        """Instantiate the stage services once (reused across questions)."""

        self._query_filter = QueryFilterService()
        self._retrieval = RetrievalService()
        self._generation = GenerationService()
        self._transcripts = TranscriptService()

    async def answer(self, request: AskIn, device_id: Optional[str]) -> AskOut:
        """Answer a question grounded in indexed pages and the open tab.

        Args:
            request: The question, conversation history and current page.
            device_id: The asking device, recorded with the logged query.

        Returns:
            The answer, the sources it cites, and the filters that were applied.
        """

        started = time.monotonic()

        # 1. Extract date/site filters from the question
        if request.no_filters:
            parsed = ParsedQuery(semantic_query=request.question)
        else:
            parsed = await self._query_filter.parse(request.question)

        # 2. Retrieve matching chunks from memory
        rows, abstain_reason = await self._retrieval.retrieve(parsed, request.top)

        # 3. Resolve the page open right now into source [0]
        current_page = await self._resolve_current_page(request.current_page)

        # 4. Generate — a live page can still answer "what is this page?"
        #    when retrieval found nothing
        cited: List[Source] = []
        if not rows and current_page is None:
            answer = abstain_reason or config.ABSTAIN_TEXT
            abstained = True
        else:
            sources = rows_to_sources(rows)
            raw_answer = await self._generation.generate(
                question=request.question,
                context=format_sources(sources, current_page),
                history=request.history,
            )

            # 5. Keep only citations of sources the model was actually shown
            citable = sources + ([current_page] if current_page else [])
            answer, cited = validate_citations(raw_answer, citable)
            abstained = False

        latency_ms = int((time.monotonic() - started) * 1000)

        # 6. Log the exchange for the evaluation harness
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

    async def _resolve_current_page(
        self,
        current_page: Optional[CurrentPageIn],
    ) -> Optional[Source]:
        """Build source [0] from the open tab, finding the best text for it.

        Args:
            current_page: What the extension sent about the open tab, if anything.

        Returns:
            The current page as source [0], or None when no tab was sent.
        """

        if current_page is None:
            return None

        text = current_page.text

        # YouTube: Readability sees page chrome; the transcript is the content
        video_id = youtube.video_id(current_page.url)
        if video_id:
            text = await self._transcripts.fetch_transcript(video_id) or text

        # extension couldn't extract (orphaned content script, PDF viewer…)
        # but the page may already be in memory — use the stored text
        if not text:
            normalized_url, _ = normalize_url(current_page.url)
            stored_text = db.page_text_for_url(normalized_url)
            if stored_text:
                text = stored_text[: config.CURRENT_PAGE_TEXT_CAP]

        return current_page_source(
            current_page.url,
            current_page.title,
            text,
            datetime.now(timezone.utc),
        )

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
        )
