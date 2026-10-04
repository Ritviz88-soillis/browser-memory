"""App entry point. Run:  uv run uvicorn main:app --port 8000"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import db
import worker
from router import get_orchestrator, router
from services.embedding_service import get_embedder

logging.basicConfig(level=logging.INFO)
# request URLs can carry credentials; keep HTTP client chatter out of the log
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the database and run the ingest worker for the app's lifetime."""

    db.init()
    logger.info("memory database: %s", config.DATABASE_PATH)

    # load the embedding model now, so the first page isn't the slow one
    await asyncio.to_thread(get_embedder, config.EMBEDDING_MODEL)

    stop = asyncio.Event()
    worker_task = asyncio.create_task(worker.run(stop, get_orchestrator()))

    yield

    stop.set()
    worker.notify()
    await worker_task
    db.close()


app = FastAPI(title="browser-memory", version="0.1.0", lifespan=lifespan)

# the side panel calls from a chrome-extension:// origin
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
