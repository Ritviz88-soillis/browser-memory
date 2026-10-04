"""Pair a browser with this server: creates a device token for the extension.

Usage:
    uv run python scripts/new_device.py --label my-chrome

Prints the bearer token ONCE; only its sha256 is stored.
"""

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).digest()

    db.init()
    try:
        device_id = db.create_device(args.label, token_hash)
    finally:
        db.close()

    print(f"device_id: {device_id}")
    print(f"token:     {token}")
    print("Paste the token into the extension's Settings; it will not be shown again.")


if __name__ == "__main__":
    main()
