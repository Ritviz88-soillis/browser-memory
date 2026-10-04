"""Pydantic models for proactive recall (POST /related)."""

from datetime import datetime
from typing import List

from pydantic import BaseModel


class RelatedPageOut(BaseModel):
    """A previously read page related to the one open now."""

    id: str
    title: str
    url: str
    domain: str
    visited: datetime
    snippet: str
    similarity: float


class RelatedOut(BaseModel):
    """Related pages, most similar first; empty when nothing is close enough."""

    pages: List[RelatedPageOut]
