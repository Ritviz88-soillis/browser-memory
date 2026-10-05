"""Background ingest worker: drains the jobs table inside the API process.

Woken the moment a page is queued (``notify``); the poll interval is only a
fallback. Failures retry up to ``config.JOB_MAX_ATTEMPTS``, then park as
'failed' with the error recorded. The indexing itself lives in
``services.ingestion_service``; this file is only the loop.
"""

import asyncio
import logging

import config
import db

logger = logging.getLogger("worker")

_wake = asyncio.Event()


def notify() -> None:
    """Wake the worker now instead of waiting for its next poll."""

    _wake.set()


async def run(stop: asyncio.Event, orchestrator) -> None:
    """Claim and index queued pages until asked to stop.

    Args:
        stop: Set by the app on shutdown to end the loop (then call ``notify``).
        orchestrator: The MemoryOrchestrator; each claimed job is dispatched
            to its ``process_job``.
    """

    logger.info("ingest worker started")

    while not stop.is_set():
        jobs = db.claim_jobs(config.WORKER_BATCH_SIZE)

        if not jobs:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=config.WORKER_POLL_SECONDS)
            except TimeoutError:
                pass
            _wake.clear()
            continue

        for job in jobs:
            try:
                await orchestrator.process_job(job["payload"], job["id"])
                db.finish_job(job["id"])
            except Exception as error:  # a bad page must not kill the loop
                logger.exception("job %s failed (attempt %s)", job["id"], job["attempts"])
                db.finish_job(job["id"], error=repr(error))

    logger.info("ingest worker stopped")
