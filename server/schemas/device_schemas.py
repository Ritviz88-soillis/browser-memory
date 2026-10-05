"""Pydantic models for device pairing (POST /pair)."""

from pydantic import BaseModel


class PairOut(BaseModel):
    """The access token issued to the extension."""

    token: str
