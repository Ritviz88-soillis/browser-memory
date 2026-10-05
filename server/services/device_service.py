"""Device pairing: how the browser extension gets its access token without the
user copying anything.

Every other endpoint needs a token, because "localhost" is not private: any
web page can send requests to it. Pairing hands a token only to a browser
extension, identified by the request's Origin — a header the browser sets and
a web page cannot forge. The first extension to pair becomes the trusted one;
a different extension asking later is refused.
"""

import hashlib
import secrets
from typing import Optional

import db

_EXTENSION_ORIGIN = "chrome-extension://"
_LABEL_PREFIX = "extension:"


class DeviceService:
    """Issues access tokens to the paired browser extension."""

    def pair(self, origin: Optional[str]) -> str:
        """Create a token for the extension making the request.

        Args:
            origin: The request's Origin header.

        Returns:
            A new bearer token (only its sha256 is stored).

        Raises:
            PermissionError: If the request does not come from a browser
                extension, or a different extension is already paired.
        """

        if not origin or not origin.startswith(_EXTENSION_ORIGIN):
            raise PermissionError("pairing is only available to the browser extension")

        extension_id = origin.removeprefix(_EXTENSION_ORIGIN).strip("/")
        trusted = {
            label.removeprefix(_LABEL_PREFIX)
            for label in db.device_labels()
            if label.startswith(_LABEL_PREFIX)
        }
        if trusted and extension_id not in trusted:
            raise PermissionError(
                "a different extension is already paired with this memory; "
                "use scripts/new_device.py to pair this one by hand"
            )

        token = secrets.token_urlsafe(32)
        db.create_device(_LABEL_PREFIX + extension_id, hashlib.sha256(token.encode()).digest())
        return token
