"""Passage selection for pages that are open right now ("live pages").

An open page is split into the same heading-aware chunks used for indexing,
and the chunks most relevant to the question are returned. Because every
source is then one specific passage, a citation can take the user to the exact
place on the page.
"""

import hashlib
import html as html_lib
from collections import OrderedDict
from typing import List, Optional, Sequence, Tuple

import numpy as np

import config
import db
from schemas import CurrentPageIn
from services.chunking_service import Chunk, ChunkingService
from services.embedding_service import get_embedder
from services.transcript_service import TranscriptService
from utils import youtube
from utils.scrub import scrub
from utils.urls import normalize_url

_LONG_PARAGRAPH_CHARS = 1500
_WRAPPED_PIECE_CHARS = 1000


def select_passages(
    chunks: Sequence[Chunk],
    similarities: Sequence[float],
    char_budget: int,
) -> List[Chunk]:
    """Choose which passages of a page to show the model.

    A short page is passed whole. For a long page the passage most similar to
    the question is taken first, then the opening passage (which says what the
    page is), then the rest by similarity while they fit the budget. Selected
    passages keep page order.

    Args:
        chunks: Every chunk of the page, in page order.
        similarities: Similarity of each chunk to the question.
        char_budget: Maximum total characters of passage text.

    Returns:
        The selected chunks, in page order.
    """

    if not chunks:
        return []
    if sum(len(chunk.text) for chunk in chunks) <= char_budget:
        return list(chunks)

    by_relevance = sorted(range(len(chunks)), key=lambda index: -similarities[index])
    best = by_relevance[0]
    priority = [best, 0] + [index for index in by_relevance[1:] if index != 0]

    # the best match is always taken, even if it alone exceeds the budget
    chosen = {best}
    used = len(chunks[best].text)
    for index in priority[1:]:
        if index in chosen or used + len(chunks[index].text) > char_budget:
            continue
        chosen.add(index)
        used += len(chunks[index].text)

    return [chunks[index] for index in sorted(chosen)]


def text_to_html(text: str) -> str:
    """Wrap plain text in paragraphs so the chunker can process it.

    Very long unpunctuated paragraphs (auto-generated video captions) are
    wrapped at word boundaries first, since they have no sentences to split on.
    """

    paragraphs: List[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentence_ends = paragraph.count(". ") + paragraph.count("? ") + paragraph.count("! ")
        if len(paragraph) > _LONG_PARAGRAPH_CHARS and sentence_ends < len(paragraph) / 400:
            paragraphs.extend(_wrap_words(paragraph, _WRAPPED_PIECE_CHARS))
        else:
            paragraphs.append(paragraph)

    return "".join(f"<p>{html_lib.escape(paragraph)}</p>" for paragraph in paragraphs)


def _wrap_words(text: str, width: int) -> List[str]:
    """Split text into pieces of roughly ``width`` characters at spaces."""

    pieces: List[str] = []
    current: List[str] = []
    length = 0
    for word in text.split():
        if current and length + len(word) + 1 > width:
            pieces.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces


class LivePageService:
    """Finds the passages of an open page that matter for a question."""

    def __init__(self, transcripts: Optional[TranscriptService] = None) -> None:
        """Set up the chunker, the transcript source and the passage cache.

        Args:
            transcripts: Transcript service to share (keeps one cache per app).
        """

        self._chunking = ChunkingService()
        self._transcripts = transcripts or TranscriptService()
        # page fingerprint -> (chunks, embedding matrix); follow-up questions
        # about the same page skip chunking and embedding
        self._cache: "OrderedDict[str, Tuple[List[Chunk], np.ndarray]]" = OrderedDict()

    async def passages(
        self,
        page: CurrentPageIn,
        question: str,
        char_budget: int = config.LIVE_PAGE_CHAR_BUDGET,
    ) -> List[Chunk]:
        """Return the passages of an open page most relevant to a question.

        Args:
            page: The open tab as sent by the extension.
            question: The topical part of the user's question.
            char_budget: Maximum total characters of passage text to return.

        Returns:
            Selected chunks in page order; empty if the page has no readable text.
        """

        # 1. Get the page's readable content as article HTML
        article_html = await self._article_html(page)
        if not article_html:
            return []

        # 2. Split into passages and embed them (cached per page content)
        chunks, matrix = await self._chunks_and_vectors(page.url, article_html)
        if not chunks:
            return []

        # 3. Score every passage against the question and pick within budget
        embedder = get_embedder(config.EMBEDDING_MODEL)
        query = np.asarray(await embedder.embed_query(question), dtype=np.float32)
        similarities = matrix @ (query / np.linalg.norm(query))

        return select_passages(chunks, similarities.tolist(), char_budget)

    async def _article_html(self, page: CurrentPageIn) -> Optional[str]:
        """Find the best available content for an open page.

        Order: a video's transcript, the article HTML the extension extracted,
        the plain text it sent, then text already stored in memory.
        """

        video_id = youtube.video_id(page.url)
        if video_id:
            transcript = await self._transcripts.fetch_transcript(video_id)
            if transcript:
                return text_to_html(transcript)

        if page.html:
            return page.html
        if page.text:
            return text_to_html(page.text)

        # extraction failed (orphaned content script, PDF viewer…) but the
        # page may already be in memory
        normalized_url, _ = normalize_url(page.url)
        stored_text = db.page_text_for_url(normalized_url)
        return text_to_html(stored_text) if stored_text else None

    async def _chunks_and_vectors(
        self,
        url: str,
        article_html: str,
    ) -> Tuple[List[Chunk], np.ndarray]:
        """Chunk and embed a page, reusing the result while its content is unchanged."""

        key = hashlib.sha256((url + "\n" + article_html).encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        _, chunks = self._chunking.chunk(scrub(article_html).text)
        if chunks:
            embedder = get_embedder(config.EMBEDDING_MODEL)
            vectors = await embedder.embed_passages([chunk.text for chunk in chunks])
            matrix = np.asarray(vectors, dtype=np.float32)
            matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)

        self._cache[key] = (chunks, matrix)
        if len(self._cache) > config.LIVE_PAGE_CACHE_MAX:
            self._cache.popitem(last=False)
        return chunks, matrix
