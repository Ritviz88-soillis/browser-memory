"""Ingest a saved HTML file into the memory corpus (no extension needed).

Usage:
    uv run python scripts/ingest_file.py page.html --url https://example.com/article

Runs the same IngestionService the background worker uses:
scrub -> chunk -> embed -> store.
"""

import argparse
import asyncio
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from services.ingestion_service import IngestionService  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--url", required=True, help="the URL this HTML came from")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    html = args.html_file.read_text(encoding="utf-8", errors="replace")

    db.init()
    try:
        # the scripts act as their own 'device' so visits have an owner
        device_id = db.create_device("cli", hashlib.sha256(b"dev-device:cli").digest())
        result = await IngestionService().process(
            {
                "device_id": device_id,
                "url": args.url,
                "title": args.title,
                "lang": None,
                "html": html,
                "visit": {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "dwell_ms": 0,
                    "source": "navigation",
                },
            }
        )
    finally:
        db.close()

    if result is None:
        print("nothing ingested (no readable content, or the site is blocked)")
        return

    status = "new content indexed" if result["new_content"] else "already indexed (unchanged)"
    print(f"{status}: page={result['page_id']}")


if __name__ == "__main__":
    asyncio.run(main())
