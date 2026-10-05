"""Indexing stage — turn text into vectors.

Two embedders, one interface. Retrieval embedders are asymmetric: a question
and a passage are embedded differently, and mixing them up degrades retrieval
silently. So every embedder exposes ``embed_passages`` and ``embed_query``
rather than a single ``embed``.

A memory database holds vectors from ONE model (``config.EMBEDDING_MODEL``):
vectors from different models are not comparable, so switching models means
re-indexing.
"""

import asyncio
import os
from typing import Any, Dict, List

import httpx
from fastembed import TextEmbedding

import config


class LocalEmbeddingService:
    """bge-small via fastembed (ONNX, CPU): unmetered, text never leaves the machine."""

    model_id = "bge-small-en-v1.5"
    dim = 384

    _FASTEMBED_NAME = "BAAI/bge-small-en-v1.5"

    def __init__(self) -> None:
        """Load the ONNX model (downloads ~130 MB on first construction)."""

        self._model = TextEmbedding(model_name=self._FASTEMBED_NAME)
        # ONNX sessions aren't guaranteed safe for concurrent runs
        self._lock = asyncio.Lock()

    async def embed_passages(self, texts: List[str]) -> List[List[float]]:
        """Embed chunk texts for storage.

        Args:
            texts: The chunk texts of one page.

        Returns:
            One vector per text, in the same order.
        """

        vectors: List[List[float]] = []
        # A few texts at a time, releasing the model between slices: a
        # question asked while a long document is being indexed gets its turn
        # after one slice instead of after the whole batch. It is also faster:
        # a big batch pads every text to the longest one.
        for start in range(0, len(texts), config.EMBEDDING_SLICE):
            piece = texts[start : start + config.EMBEDDING_SLICE]
            async with self._lock:
                # CPU-bound: keep it off the event loop
                vectors += await asyncio.to_thread(
                    lambda: [vector.tolist() for vector in self._model.passage_embed(piece)]
                )
        return vectors

    async def embed_query(self, text: str) -> List[float]:
        """Embed a user question for search.

        Args:
            text: The question (or its topical part).

        Returns:
            The query vector.
        """

        async with self._lock:
            return await asyncio.to_thread(
                lambda: next(iter(self._model.query_embed([text]))).tolist()
            )


class JinaEmbeddingService:
    """Jina embeddings v3 — optional hosted embedder (free key at jina.ai).

    Matryoshka: ``dimensions: 512`` truncates server-side to match the
    ``emb_jina_512`` column.
    """

    model_id = "jina-embeddings-v3"
    dim = 512

    _URL = "https://api.jina.ai/v1/embeddings"

    def __init__(self) -> None:
        """Create the HTTP client.

        Raises:
            RuntimeError: If JINA_API_KEY is not set.
        """

        key = os.environ.get("JINA_API_KEY")
        if not key:
            raise RuntimeError("JINA_API_KEY is not set (get a free key at jina.ai)")

        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def _embed(self, texts: List[str], task: str) -> List[List[float]]:
        """Call the Jina API for one batch with the given retrieval task."""

        if not texts:
            return []

        response = await self._client.post(
            self._URL,
            json={
                "model": self.model_id,
                "task": task,
                "dimensions": self.dim,
                "input": texts,
            },
        )
        response.raise_for_status()
        data: Dict[str, Any] = response.json()

        # input order across the data array is not a documented guarantee
        items = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]

    async def embed_passages(self, texts: List[str]) -> List[List[float]]:
        """Embed chunk texts for storage.

        Args:
            texts: The chunk texts of one page.

        Returns:
            One vector per text, in the same order.
        """

        return await self._embed(texts, task="retrieval.passage")

    async def embed_query(self, text: str) -> List[float]:
        """Embed a user question for search.

        Args:
            text: The question (or its topical part).

        Returns:
            The query vector.
        """

        return (await self._embed([text], task="retrieval.query"))[0]


_EMBEDDERS = {
    LocalEmbeddingService.model_id: LocalEmbeddingService,
    JinaEmbeddingService.model_id: JinaEmbeddingService,
}

_instances: Dict[str, Any] = {}


def get_embedder(model_id: str):
    """Return the shared embedder for a model id.

    One instance per model: the local embedder holds a loaded ONNX model and
    the Jina embedder an HTTP client — neither should be built per call.

    Args:
        model_id: ``"bge-small-en-v1.5"`` or ``"jina-embeddings-v3"``.

    Returns:
        The embedder for that model.

    Raises:
        ValueError: If the model id is not known.
    """

    if model_id not in _instances:
        if model_id not in _EMBEDDERS:
            raise ValueError(
                f"unknown embedding model {model_id!r}; known: {sorted(_EMBEDDERS)}"
            )
        _instances[model_id] = _EMBEDDERS[model_id]()

    return _instances[model_id]


class EmbeddingService:
    """The embedding stage, using the model this memory was built with."""

    def __init__(self) -> None:
        """Load the configured embedder (shared across the app)."""

        self._embedder = get_embedder(config.EMBEDDING_MODEL)
        self.model_id = self._embedder.model_id

    async def embed_passages(self, texts: List[str]) -> List[List[float]]:
        """Embed page text for storage or comparison.

        Args:
            texts: Passages of page text.

        Returns:
            One vector per passage, in the same order.
        """

        return await self._embedder.embed_passages(texts)

    async def embed_query(self, text: str) -> List[float]:
        """Embed a user question for search.

        Args:
            text: The question (or its topical part).

        Returns:
            The query vector.
        """

        return await self._embedder.embed_query(text)
