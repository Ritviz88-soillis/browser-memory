"""The parsed form of a question, shared by the filter and retrieval stages."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ParsedQuery(BaseModel):
    """A question split into its topical part and its retrieval filters."""

    semantic_query: str
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    domains: Optional[List[str]] = None
