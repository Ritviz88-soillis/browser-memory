"""Ask a question against the ingested corpus (no extension needed).

Usage:
    uv run python scripts/ask.py "what did I read about MVCC yesterday?"
    uv run python scripts/ask.py "..." --no-parse   # skip filter extraction

Runs the same orchestrator the API uses: parse filters -> hybrid retrieval ->
grounded generation -> citation validation.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db  # noqa: E402
from orchestrator import MemoryOrchestrator  # noqa: E402
from schemas import AskIn  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--top", type=int, default=config.RETRIEVAL_TOP_K)
    parser.add_argument("--no-parse", action="store_true", help="skip filter extraction")
    args = parser.parse_args()

    db.init()
    try:
        result = await MemoryOrchestrator().ask(
            AskIn(question=args.question, top=args.top, no_filters=args.no_parse),
            device_id=None,
        )
    finally:
        db.close()

    filters = result.filters
    if filters.since or filters.until or filters.domains:
        bits = []
        if filters.since:
            bits.append(f"since {filters.since:%Y-%m-%d}")
        if filters.until:
            bits.append(f"until {filters.until:%Y-%m-%d}")
        if filters.domains:
            bits.append(f"domains {filters.domains}")
        print(f"[filters: {', '.join(bits)}; query: {filters.semantic_query!r}]")

    print(result.answer)
    if result.sources:
        print("\nSources:")
        for source in result.sources:
            print(f"  [{source.n}] {source.title} - {source.url} (visited {source.visited:%Y-%m-%d})")


if __name__ == "__main__":
    asyncio.run(main())
