"""Pydantic models for page ingestion (POST /ingest)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

import config


class VisitIn(BaseModel):
    """How the user visited the page being ingested."""

    started_at: datetime
    last_active_at: Optional[datetime] = None
    dwell_ms: int = Field(0, ge=0)
    scroll_depth_pct: Optional[int] = Field(None, ge=0, le=100)
    referrer_url: Optional[str] = None
    tab_id: Optional[int] = None
    source: Literal["navigation", "history_state", "reactivation"] = "navigation"


class IngestIn(BaseModel):
    """A page the extension extracted and wants indexed."""

    idempotency_key: str = Field(min_length=8, max_length=128)
    url: str = Field(min_length=10, max_length=4096)
    title: Optional[str] = Field(None, max_length=1000)
    lang: Optional[str] = Field(None, max_length=16)
    html: str = Field(min_length=1, max_length=config.INGEST_HTML_MAX_CHARS)
    visit: VisitIn
    model: str = config.EMBEDDING_MODEL


class IngestPdfIn(BaseModel):
    """A PDF the user is reading. The browser's PDF viewer cannot be read by
    an extension, so the file itself is sent (base64) and read on the server."""

    idempotency_key: str = Field(min_length=8, max_length=128)
    url: str = Field(min_length=10, max_length=4096)
    title: Optional[str] = Field(None, max_length=1000)
    # base64 is 4/3 the size of the file it encodes
    pdf_base64: str = Field(min_length=1, max_length=config.PDF_MAX_BYTES * 4 // 3 + 4)
    visit: VisitIn


class IngestOut(BaseModel):
    """Whether the page was queued or was already in the queue."""

    queued: bool
    duplicate: bool
