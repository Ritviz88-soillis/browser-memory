"""Pydantic models for the indexed-pages panel (status, list, forget)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class StatusOut(BaseModel):
    """Live indexing counters shown in the side panel header."""

    pending_jobs: int
    failed_jobs: int
    pages: int
    chunks: int


class PageOut(BaseModel):
    """One indexed page."""

    id: str
    url: str
    domain: str
    title: Optional[str]
    word_count: int
    last_visited_at: datetime
    indexed_at: datetime


class ForgetOut(BaseModel):
    """How many pages a forget request removed."""

    deleted: int
