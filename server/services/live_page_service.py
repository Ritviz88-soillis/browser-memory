"""Passage selection for pages that are open right now ("live pages").

An open page is split into passages and the ones most relevant to the
question are shown to the model, so a citation can take the user to the exact
place on the page.

This service only decides: which content represents the page, and which of
its passages to keep. Chunking and embedding are done by their own services;
the orchestrator passes their results in.
"""

import hashlib
import html as html_lib
from collections import OrderedDict
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

import config
from schemas import CurrentPageIn

_LONG_PARAGRAPH_CHARS = 1500
_WRAPPED_PIECE_CHARS = 1000


def select_passages(
    chunks: Sequence[Any],
    similarities: Sequence[float],
    char_budget: int,
) -> List[Any]:
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
    """Decides what an open page contributes to an answer."""

    def __init__(self) -> None:
        """Start with an empty passage cache."""

        # page fingerprint -> (chunks, unit-length embedding matrix), so
        # follow-up questions about the same page skip chunking and embedding
        self._cache: "OrderedDict[str, Tuple[List[Any], np.ndarray]]" = OrderedDict()

    def article_html(
        self,
        page: CurrentPageIn,
        transcript: Optional[str] = None,
        stored_text: Optional[str] = None,
    ) -> Optional[str]:
        """Pick the best available content for an open page, as article HTML.

        Order: a video's transcript, the article HTML the extension extracted,
        the plain text it sent, then text already stored in memory.

        Args:
            page: The open tab as sent by the extension.
            transcript: The video transcript, if the page is a video.
            stored_text: The page's text from memory, if it was indexed before.

        Returns:
            HTML ready for the chunker, or None if the page has nothing readable.
        """

        if transcript:
            return text_to_html(transcript)
        if page.html:
            return page.html
        if page.text:
            return text_to_html(page.text)
        if stored_text:
            return text_to_html(stored_text)
        return None

    def cached_passages(
        self,
        url: str,
        article_html: str,
    ) -> Optional[Tuple[List[Any], np.ndarray]]:
        """Return a page's chunks and embeddings if its content was seen before.

        Args:
            url: The page URL.
            article_html: The page content chosen by ``article_html``.

        Returns:
            The cached (chunks, embedding matrix), or None.
        """

        key = self._fingerprint(url, article_html)
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def cache_passages(
        self,
        url: str,
        article_html: str,
        chunks: List[Any],
        vectors: Sequence[Sequence[float]],
    ) -> Tuple[List[Any], np.ndarray]:
        """Remember a page's chunks and embeddings for follow-up questions.

        Args:
            url: The page URL.
            article_html: The page content the chunks were made from.
            chunks: The page's chunks, in page order.
            vectors: One embedding per chunk.

        Returns:
            The (chunks, unit-length embedding matrix) that was stored.
        """

        if chunks:
            matrix = np.asarray(vectors, dtype=np.float32)
            matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)

        self._cache[self._fingerprint(url, article_html)] = (chunks, matrix)
        if len(self._cache) > config.LIVE_PAGE_CACHE_MAX:
            self._cache.popitem(last=False)
        return chunks, matrix

    def select(
        self,
        chunks: List[Any],
        matrix: np.ndarray,
        query_vector: Sequence[float],
        char_budget: int = config.LIVE_PAGE_CHAR_BUDGET,
    ) -> List[Any]:
        """Keep the passages of a page most relevant to the question.

        Args:
            chunks: The page's chunks, in page order.
            matrix: Their unit-length embeddings (from ``cache_passages``).
            query_vector: The embedding of the question.
            char_budget: Maximum total characters of passage text to keep.

        Returns:
            The selected chunks, in page order.
        """

        if not chunks:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        similarities = matrix @ (query / np.linalg.norm(query))
        return select_passages(chunks, similarities.tolist(), char_budget)

    def _fingerprint(self, url: str, article_html: str) -> str:
        """Identify one version of one page's content."""

        return hashlib.sha256((url + "\n" + article_html).encode()).hexdigest()
