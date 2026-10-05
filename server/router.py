"""Browser-memory router — the HTTP endpoints the extension calls.

Thin by convention: each endpoint hands its request to the orchestrator.
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Header, HTTPException

from orchestrator import MemoryOrchestrator
from schemas import (
    AskIn,
    AskOut,
    CurrentPageIn,
    ForgetOut,
    IngestIn,
    IngestOut,
    PageOut,
    PairOut,
    RelatedOut,
    StatusOut,
)
from utils.auth import DeviceId
from utils.llm import LLMUnavailable

router = APIRouter()

_orchestrator = MemoryOrchestrator()


def get_orchestrator() -> MemoryOrchestrator:
    """The shared orchestrator (the background worker uses the same one)."""

    return _orchestrator


@router.get("/", include_in_schema=False)
async def root() -> dict:
    """Describe the service for anyone opening the server URL in a browser."""

    return {
        "service": "browser-memory",
        "docs": "/docs",
        "health": "/health",
        "hint": "authenticated endpoints: POST /ask, POST /ingest, GET /pages",
    }


@router.get("/health")
async def health() -> dict:
    """Report whether the server and its database are reachable."""

    return await _orchestrator.health_check()


@router.post("/pair")
async def pair(origin: Annotated[Optional[str], Header()] = None) -> PairOut:
    """Issue an access token to the browser extension (no token needed to call)."""

    try:
        return await _orchestrator.pair(origin)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error


@router.post("/ingest", status_code=202)
async def ingest(body: IngestIn, device_id: DeviceId) -> IngestOut:
    """Queue an extracted page for indexing; returns immediately."""

    try:
        return await _orchestrator.ingest(body, device_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/ask")
async def ask(body: AskIn, device_id: DeviceId) -> AskOut:
    """Answer a question from browsing memory, with cited sources."""

    try:
        return await _orchestrator.ask(body, device_id)
    except LLMUnavailable as error:
        raise HTTPException(503, str(error)) from error


@router.post("/related")
async def related(body: CurrentPageIn, device_id: DeviceId) -> RelatedOut:
    """Return previously read pages related to the page open now (no LLM call)."""

    return await _orchestrator.related(body)


@router.get("/status")
async def status(device_id: DeviceId) -> StatusOut:
    """Return live indexing counters for the side panel header."""

    return await _orchestrator.status()


@router.get("/pages")
async def pages(
    device_id: DeviceId,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
) -> List[PageOut]:
    """List indexed pages, optionally filtered by a title/URL substring."""

    return await _orchestrator.list_pages(limit, offset, q)


@router.delete("/pages/{page_id}")
async def forget_page(page_id: str, device_id: DeviceId) -> ForgetOut:
    """Delete one indexed page."""

    deleted = await _orchestrator.forget_page(page_id)
    if not deleted:
        raise HTTPException(404, "no such page")
    return ForgetOut(deleted=1)


@router.delete("/sites/{domain}")
async def forget_site(domain: str, device_id: DeviceId) -> ForgetOut:
    """Block a site and delete every page indexed from it."""

    return ForgetOut(deleted=await _orchestrator.forget_site(domain))
