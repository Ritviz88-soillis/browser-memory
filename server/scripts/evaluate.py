"""Retrieval evaluation: how often does search find the page that answers a question?

Usage:
    uv run python scripts/evaluate.py             # reuse the evaluation index if present
    uv run python scripts/evaluate.py --rebuild   # fetch pages and index them again

It indexes the pages listed in evaluation/questions.json into a SEPARATE
database (your own memory is not touched), then runs every question through
three search modes:

    vector    embeddings only
    keyword   full-text search (BM25) only
    hybrid    both, fused with Reciprocal Rank Fusion (what the app uses)

For each mode it reports how often a passage from a correct page is ranked
first (hit@1), in the top 3 (hit@3) and in the top 6 (hit@6, what the model is
shown), plus the mean reciprocal rank (MRR). No language model is called.

Results are printed and written to evaluation/results.md and results.json.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

SERVER = Path(__file__).resolve().parent.parent
EVALUATION = SERVER / "evaluation"
CACHE = EVALUATION / "cache"

# a separate database, set BEFORE config is imported
os.environ["MEMORY_DB_PATH"] = str(CACHE / "evaluation.db")
sys.path.insert(0, str(SERVER))

import httpx  # noqa: E402
from selectolax.parser import HTMLParser  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
from orchestrator import MemoryOrchestrator  # noqa: E402
from utils.urls import normalize_url  # noqa: E402

MODES = {
    "vector": {"use_vectors": True, "use_keywords": False},
    "keyword": {"use_vectors": False, "use_keywords": True},
    "hybrid": {"use_vectors": True, "use_keywords": True},
}
DEPTH = 10  # how far down the ranking to look for a correct page
USER_AGENT = "browser-memory-evaluation/0.1 (student project; fetches each page once)"


def fetch(url: str) -> str:
    """Download a page once; later runs read the cached copy."""

    cached = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".html")
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    response = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60)
    response.raise_for_status()
    cached.write_text(response.text, encoding="utf-8")
    time.sleep(0.5)  # be polite to the site
    return response.text


def article(html: str) -> tuple[str, str]:
    """The page's title and main content, leaving out navigation and sidebars
    (the job Readability does in the browser)."""

    tree = HTMLParser(html)
    title = tree.css_first("title").text(strip=True) if tree.css_first("title") else ""
    for selector in ('div.body[role="main"]', "main", "div.document", "body"):
        node = tree.css_first(selector)
        if node is not None:
            return title, node.html
    return title, html


async def build_index(orchestrator: MemoryOrchestrator, pages: List[str]) -> None:
    for url in pages:
        title, html = article(fetch(url))
        result = await orchestrator.process_job(
            {
                "device_id": "evaluation",
                "url": url,
                "title": title,
                "html": html,
                "visit": {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "dwell_ms": 0,
                    "source": "navigation",
                },
            }
        )
        print(f"  indexed {'new' if result and result['new_content'] else 'kept'}: {url}")


async def run_mode(
    orchestrator: MemoryOrchestrator,
    questions: List[Dict[str, Any]],
    flags: Dict[str, bool],
) -> List[Dict[str, Any]]:
    """For each question: the rank of the first passage from a correct page."""

    outcomes = []
    for item in questions:
        gold = {normalize_url(url)[0] for url in item["gold"]}
        started = time.perf_counter()
        rows = await orchestrator.retrieve(item["q"], top=DEPTH, **flags)
        elapsed_ms = (time.perf_counter() - started) * 1000
        rank = next((i for i, row in enumerate(rows, start=1) if row["url"] in gold), None)
        outcomes.append({"q": item["q"], "kind": item["kind"], "rank": rank, "ms": elapsed_ms})
    return outcomes


def summarise(outcomes: List[Dict[str, Any]]) -> Dict[str, float]:
    def hit(k: int) -> float:
        return mean(1.0 if o["rank"] and o["rank"] <= k else 0.0 for o in outcomes)

    return {
        "n": len(outcomes),
        "hit@1": hit(1),
        "hit@3": hit(3),
        "hit@6": hit(6),
        "mrr": mean(1.0 / o["rank"] if o["rank"] else 0.0 for o in outcomes),
        "ms": mean(o["ms"] for o in outcomes),
    }


def table(title: str, rows: Dict[str, Dict[str, float]]) -> str:
    lines = [
        f"**{title}**",
        "",
        "| Search mode | Questions | hit@1 | hit@3 | hit@6 | MRR | ms per query |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode, s in rows.items():
        lines.append(
            f"| {mode} | {s['n']} | {s['hit@1']:.0%} | {s['hit@3']:.0%} | {s['hit@6']:.0%} "
            f"| {s['mrr']:.3f} | {s['ms']:.0f} |"
        )
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="fetch and index the pages again")
    args = parser.parse_args()

    spec = json.loads((EVALUATION / "questions.json").read_text(encoding="utf-8"))
    questions, pages = spec["questions"], spec["pages"]

    CACHE.mkdir(parents=True, exist_ok=True)
    if args.rebuild:
        for leftover in CACHE.glob("evaluation.db*"):
            leftover.unlink()

    db.init()
    orchestrator = MemoryOrchestrator()
    try:
        if db.status_counts()["pages"] < len(pages):
            print(f"Indexing {len(pages)} pages into the evaluation database...")
            await build_index(orchestrator, pages)
        counts = db.status_counts()
        await orchestrator.retrieve("warm up")

        results = {mode: await run_mode(orchestrator, questions, flags) for mode, flags in MODES.items()}
    finally:
        db.close()

    overall = {mode: summarise(outcomes) for mode, outcomes in results.items()}
    by_kind = {
        kind: {
            mode: summarise([o for o in outcomes if o["kind"] == kind])
            for mode, outcomes in results.items()
        }
        for kind in ("keyword", "paraphrase")
    }
    missed = [o["q"] for o in results["hybrid"] if not o["rank"] or o["rank"] > 6]

    report = "\n\n".join(
        [
            "# Retrieval evaluation",
            f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/evaluate.py`. "
            f"Corpus: {counts['pages']} pages, {counts['chunks']} passages. "
            f"Embedding model: `{config.EMBEDDING_MODEL}`. "
            f"A question counts as a hit at k when a passage from a page that answers it "
            f"is among the top k results.",
            table("All questions", overall),
            table("Questions using the page's own terms", by_kind["keyword"]),
            table("Questions that paraphrase the idea", by_kind["paraphrase"]),
            "**Questions hybrid search did not answer within the top 6**\n\n"
            + ("\n".join(f"- {q}" for q in missed) if missed else "None."),
        ]
    )
    print("\n" + report)

    (EVALUATION / "results.md").write_text(report + "\n", encoding="utf-8")
    (EVALUATION / "results.json").write_text(
        json.dumps({"corpus": counts, "overall": overall, "by_kind": by_kind, "questions": results}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWritten to {EVALUATION / 'results.md'}")


if __name__ == "__main__":
    asyncio.run(main())
