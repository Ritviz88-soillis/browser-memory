"""Pydantic models for question answering (POST /ask)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

import config


class HistoryTurn(BaseModel):
    """One earlier turn of the conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class CurrentPageIn(BaseModel):
    """The tab open next to the side panel, so "this page" questions work.

    The extension applies the same privacy gates as indexing before sending.
    """

    url: str = Field(min_length=1, max_length=4096)
    title: Optional[str] = Field(None, max_length=1000)
    text: Optional[str] = Field(None, max_length=config.CURRENT_PAGE_TEXT_CAP)


class AskIn(BaseModel):
    """A question, with optional conversation history and current page."""

    question: str = Field(min_length=1, max_length=1000)
    top: int = Field(config.RETRIEVAL_TOP_K, ge=1, le=12)
    no_filters: bool = False
    # last few turns for follow-up questions; capped to bound prompt size
    history: List[HistoryTurn] = Field(default_factory=list, max_length=6)
    current_page: Optional[CurrentPageIn] = None


class FilterOut(BaseModel):
    """The filters extracted from the question, echoed back to the UI."""

    semantic_query: str
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    domains: Optional[List[str]] = None


class SourceOut(BaseModel):
    """A source the answer actually cites."""

    n: int
    title: str
    url: str
    domain: str
    heading_path: List[str]
    visited: datetime
    snippet: str


class AskOut(BaseModel):
    """The grounded answer with its cited sources."""

    answer: str
    abstained: bool
    sources: List[SourceOut]
    filters: FilterOut
    latency_ms: int
