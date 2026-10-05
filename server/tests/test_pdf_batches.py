"""A long PDF is indexed in batches of pages, each saved before the next
starts. These tests run a 25-page document through the real orchestrator and
check the three properties that matter: early pages are searchable before the
document is finished, a stopped job resumes without redoing or losing work,
and the same file opened again is not indexed twice.

No language model is called (indexing does not use one).
"""

import base64
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
from test_pdf import make_pdf

pytestmark = pytest.mark.skipif(
    not any(
        os.environ.get(key)
        for key in ("GROQ_API_KEY", "HUGGINGFACEHUB_API_TOKEN", "GOOGLE_API_KEY")
    ),
    reason="no LLM key configured (the orchestrator builds its chat model at startup)",
)

URL = "https://papers.example/long-study.pdf"
PAGES = 25
BOOK = make_pdf(
    [
        [
            "Annual Review of Testing",
            f"Chapter {number} discusses topic number {number} in careful detail.",
            f"The keyword for this chapter is marker{number:03d}.",
            str(number),
        ]
        for number in range(1, PAGES + 1)
    ]
)


def request(key: str, data: bytes = BOOK):
    from schemas import IngestPdfIn, VisitIn

    return IngestPdfIn(
        idempotency_key=key,
        url=URL,
        title="long-study.pdf",
        pdf_base64=base64.b64encode(data).decode(),
        visit=VisitIn(started_at=datetime(2026, 10, 5, tzinfo=timezone.utc), dwell_ms=60_000),
    )


@pytest.fixture
def orchestrator(memory):
    from orchestrator import MemoryOrchestrator

    return MemoryOrchestrator()


def pages_in_memory(memory) -> set:
    """Page markers present in the stored passages. A long page gets "Page N"
    as its heading; these short test pages are merged several to a passage,
    which then carries the markers in its text."""

    stored = memory.page_chunks(URL)
    found = set()
    for chunk in stored["chunks"] if stored else []:
        found.update(h for h in chunk["heading_path"] if h.startswith("Page "))
        found.update(re.findall(r"(?:^|\n\n)(Page \d+)(?=\n\n|$)", chunk["text"]))
    return found


async def test_long_pdf_is_indexed_in_batches_with_every_page(orchestrator, memory):
    assert (await orchestrator.ingest_pdf(request("batch-key-0001"), "device")).queued is True
    job = memory.claim_jobs(1)[0]
    assert Path(job["payload"]["pdf_path"]).exists(), "the file is parked, not read at upload time"

    result = await orchestrator.process_job(job["payload"], job["id"])
    memory.finish_job(job["id"])

    assert result["new_content"] is True
    assert pages_in_memory(memory) == {f"Page {n}" for n in range(1, PAGES + 1)}
    assert not Path(job["payload"]["pdf_path"]).exists(), "the parked file is removed when done"

    stored = memory.page_chunks(URL)
    text = memory.page_text_for_url(URL)
    assert "Annual Review of Testing" not in text, "the running header was removed"
    assert all(chunk["text"] in text for chunk in stored["chunks"]), "passages match the stored text"
    assert memory.status_counts()["pages"] == 1, "one document, however many batches"

    # the last page is findable by its own keyword
    rows = memory.hybrid_search([1.0] + [0.0] * 383, "marker025", use_vectors=False)
    assert rows and "Page 25" in rows[0]["text"]


async def test_a_stopped_job_resumes_where_it_left_off(orchestrator, memory, monkeypatch):
    await orchestrator.ingest_pdf(request("batch-key-0002"), "device")
    job = memory.claim_jobs(1)[0]

    # the server "stops" while embedding the second batch
    embed = orchestrator._embedding.embed_passages
    calls = {"count": 0}

    async def fail_on_second_batch(texts):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("server stopped")
        return await embed(texts)

    monkeypatch.setattr(orchestrator._embedding, "embed_passages", fail_on_second_batch)
    with pytest.raises(RuntimeError):
        await orchestrator.process_job(job["payload"], job["id"])
    memory.finish_job(job["id"], error="server stopped")

    # the first batch is already saved and searchable
    first_batch = {f"Page {n}" for n in range(1, config.PDF_PAGES_PER_BATCH + 1)}
    assert pages_in_memory(memory) == first_batch
    chunks_after_first_batch = memory.status_counts()["chunks"]

    # the job is picked up again and continues from the saved progress
    monkeypatch.setattr(orchestrator._embedding, "embed_passages", embed)
    retried = memory.claim_jobs(1)[0]
    assert retried["id"] == job["id"]
    assert retried["payload"]["progress"]["pages_done"] == config.PDF_PAGES_PER_BATCH

    await orchestrator.process_job(retried["payload"], retried["id"])
    memory.finish_job(retried["id"])

    assert pages_in_memory(memory) == {f"Page {n}" for n in range(1, PAGES + 1)}
    stored = memory.page_chunks(URL)
    texts = [chunk["text"] for chunk in stored["chunks"]]
    assert len(texts) == len(set(texts)), "no batch was stored twice"
    assert len(texts) > chunks_after_first_batch
    assert memory.status_counts()["pages"] == 1


async def test_the_same_file_opened_again_is_not_indexed_twice(orchestrator, memory):
    await orchestrator.ingest_pdf(request("batch-key-0003"), "device")
    job = memory.claim_jobs(1)[0]
    await orchestrator.process_job(job["payload"], job["id"])
    memory.finish_job(job["id"])
    chunks = memory.status_counts()["chunks"]

    await orchestrator.ingest_pdf(request("batch-key-0004"), "device")
    again = memory.claim_jobs(1)[0]
    result = await orchestrator.process_job(again["payload"], again["id"])

    assert result == {"page_id": result["page_id"], "new_content": False}
    assert memory.status_counts()["chunks"] == chunks
    assert not Path(again["payload"]["pdf_path"]).exists()


async def test_a_file_that_is_not_a_pdf_is_refused_at_once(orchestrator):
    with pytest.raises(ValueError, match="could not be read as a PDF"):
        await orchestrator.ingest_pdf(request("batch-key-0005", data=b"<html>not a pdf</html>"), "device")


async def test_a_pdf_with_no_text_is_skipped_cleanly(orchestrator, memory):
    await orchestrator.ingest_pdf(request("batch-key-0006", data=make_pdf([[], [], []])), "device")
    job = memory.claim_jobs(1)[0]
    assert await orchestrator.process_job(job["payload"], job["id"]) is None
    assert memory.status_counts()["pages"] == 0
    assert not Path(job["payload"]["pdf_path"]).exists()
