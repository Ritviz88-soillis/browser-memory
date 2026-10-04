"""Device authentication: ``Authorization: Bearer <token>``, matched by sha256
against ``devices.token_hash``. Raw tokens are never stored or logged."""

import hashlib
from typing import Annotated, Optional

from cachetools import TTLCache
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db

_bearer = HTTPBearer(auto_error=False)
_device_cache: TTLCache[bytes, str] = TTLCache(maxsize=64, ttl=300)


async def require_device(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)],
) -> str:
    """Resolve the bearer token to a device id.

    Args:
        credentials: The parsed Authorization header, if one was sent.

    Returns:
        The id of the paired device.

    Raises:
        HTTPException: 401 when the token is missing or unknown.
    """

    if credentials is None:
        raise HTTPException(401, "missing bearer token")

    token_hash = hashlib.sha256(credentials.credentials.encode()).digest()
    if (device_id := _device_cache.get(token_hash)) is not None:
        return device_id

    device_id = db.get_device_id(token_hash)
    if device_id is None:
        raise HTTPException(401, "unknown device token")

    _device_cache[token_hash] = str(device_id)
    return str(device_id)


DeviceId = Annotated[str, Depends(require_device)]
