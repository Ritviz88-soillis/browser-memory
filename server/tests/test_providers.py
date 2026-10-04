"""Provider tests.

LocalBGE tests are real (they download ~130 MB of ONNX weights on first run,
cached afterwards) — a mocked embedding test proves nothing about the property
retrieval depends on: that related text lands closer than unrelated text.
Jina tests run only when JINA_API_KEY is set, so the default suite never
depends on the network.
"""

import math
import os

import pytest

from services.embedding_service import get_embedder


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


PASSAGES = [
    "PostgreSQL uses multi-version concurrency control to let readers and writers proceed without blocking each other.",
    "The recipe calls for two cups of flour, a pinch of salt, and fresh rosemary.",
]


async def test_local_bge_dims_and_semantics():
    emb = get_embedder("bge-small-en-v1.5")
    vecs = await emb.embed_passages(PASSAGES)
    assert len(vecs) == 2 and all(len(v) == 384 for v in vecs)

    q = await emb.embed_query("how does postgres handle concurrent transactions?")
    assert len(q) == 384
    # The database question must be closer to the database passage than to the
    # recipe. If this fails, the query/passage prefixes are likely mixed up.
    assert cosine(q, vecs[0]) > cosine(q, vecs[1]) + 0.05


async def test_local_bge_empty_batch():
    emb = get_embedder("bge-small-en-v1.5")
    assert await emb.embed_passages([]) == []


def test_registry_rejects_unknown_model():
    with pytest.raises(ValueError, match="unknown embedding model"):
        get_embedder("text-embedding-3-small")


def test_registry_caches_instances():
    a = get_embedder("bge-small-en-v1.5")
    b = get_embedder("bge-small-en-v1.5")
    assert a is b, "providers must be singletons (model load is expensive)"


def test_configured_embedder_exists():
    import config

    emb = get_embedder(config.EMBEDDING_MODEL)
    assert emb.model_id == config.EMBEDDING_MODEL and emb.dim == 384


@pytest.mark.skipif(not os.environ.get("JINA_API_KEY"), reason="JINA_API_KEY not set")
async def test_jina_dims_and_semantics():
    emb = get_embedder("jina-embeddings-v3")
    vecs = await emb.embed_passages(PASSAGES)
    assert len(vecs) == 2 and all(len(v) == 512 for v in vecs)
    q = await emb.embed_query("how does postgres handle concurrent transactions?")
    assert len(q) == 512
    assert cosine(q, vecs[0]) > cosine(q, vecs[1])
