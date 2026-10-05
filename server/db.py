"""Data layer: one local SQLite file holds everything the server remembers.

No database server, no network. The schema below is created automatically on
first start. Two kinds of search run over the stored chunks:

  * keyword search — SQLite's built-in FTS5 full-text index;
  * vector search  — every chunk's embedding is kept in one in-memory numpy
    matrix, so cosine similarity is a single matrix product. At personal
    browsing scale (thousands of chunks) this takes about a millisecond.

Every call here finishes in a few milliseconds, so the functions are plain
synchronous ``def``s called directly from the async services.
"""

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id           TEXT PRIMARY KEY,
  label        TEXT NOT NULL,
  token_hash   BLOB NOT NULL UNIQUE,      -- sha256 of the bearer token
  created_at   TEXT NOT NULL,
  last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS pages (
  id               TEXT PRIMARY KEY,
  url              TEXT NOT NULL UNIQUE,  -- normalized (tracking params removed)
  domain           TEXT NOT NULL,
  title            TEXT,
  lang             TEXT,
  extracted_text   TEXT NOT NULL,         -- canonical text; chunk offsets refer to it
  content_hash     TEXT NOT NULL,         -- sha256 of extracted_text, for dedup
  word_count       INTEGER NOT NULL,
  read_score       REAL NOT NULL DEFAULT 0,
  first_visited_at TEXT NOT NULL,
  last_visited_at  TEXT NOT NULL,
  indexed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pages_domain       ON pages(domain);
CREATE INDEX IF NOT EXISTS pages_last_visited ON pages(last_visited_at);

CREATE TABLE IF NOT EXISTS chunks (
  id           INTEGER PRIMARY KEY,
  page_id      TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  ordinal      INTEGER NOT NULL,
  heading_path TEXT NOT NULL,             -- JSON list of headings above the chunk
  text         TEXT NOT NULL,
  token_count  INTEGER NOT NULL,
  char_start   INTEGER NOT NULL,
  char_end     INTEGER NOT NULL,
  embedding    BLOB NOT NULL              -- float32 vector
);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks(page_id);

-- keyword index over chunk text; rowid = chunks.id
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
  USING fts5(text, tokenize = 'porter unicode61');

CREATE TABLE IF NOT EXISTS visits (
  id               INTEGER PRIMARY KEY,
  page_id          TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  device_id        TEXT,
  raw_url          TEXT,
  started_at       TEXT NOT NULL,
  dwell_ms         INTEGER NOT NULL DEFAULT 0,
  scroll_depth_pct INTEGER,
  source           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS visits_page ON visits(page_id);

-- "forget this site": its pages are deleted and it is never indexed again
CREATE TABLE IF NOT EXISTS blocked_sites (
  domain     TEXT PRIMARY KEY,
  blocked_at TEXT NOT NULL
);

-- indexing queue: the API records a job, the background worker processes it
CREATE TABLE IF NOT EXISTS jobs (
  id              INTEGER PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
  payload         TEXT NOT NULL,                    -- JSON; emptied once done
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  claimed_at      TEXT,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

-- every question and what was retrieved, for the evaluation harness
CREATE TABLE IF NOT EXISTS queries (
  id            INTEGER PRIMARY KEY,
  device_id     TEXT,
  question      TEXT NOT NULL,
  parsed_filter TEXT,
  chunk_ids     TEXT,
  answer        TEXT,
  abstained     INTEGER NOT NULL,
  embed_model   TEXT,
  llm_model     TEXT,
  latency_ms    INTEGER,
  created_at    TEXT NOT NULL
);
"""

_STOPWORDS = frozenset(
    "a an and are as at be but by did do does for from had has have how i in is it "
    "me my of on or that the this to was were what when where which who why with you".split()
)
_WORD_RE = re.compile(r"\w+")

_connection: Optional[sqlite3.Connection] = None
# (chunk ids, unit-length embedding matrix); rebuilt lazily after any write
_vector_index: Optional[Tuple[np.ndarray, np.ndarray]] = None


@dataclass(slots=True)
class ChunkRow:
    """One chunk ready to store."""

    ordinal: int
    heading_path: List[str]
    text: str
    token_count: int
    char_start: int
    char_end: int
    embedding: List[float]


# --- connection ------------------------------------------------------------


def init() -> None:
    """Open the database file and create the schema. Call once at startup."""

    global _connection
    if _connection is not None:
        return

    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)

    # a job left 'running' means the server stopped mid-page: retry it
    connection.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
    connection.commit()
    _connection = connection


def close() -> None:
    """Close the database file. Call once at shutdown."""

    global _connection, _vector_index
    if _connection is not None:
        _connection.close()
    _connection = None
    _vector_index = None


def _db() -> sqlite3.Connection:
    """The open connection."""

    if _connection is None:
        raise RuntimeError("db.init() has not been called")
    return _connection


def ping() -> None:
    """Health probe: raises if the database is unusable."""

    _db().execute("SELECT 1").fetchone()


def _now() -> str:
    """Current UTC time in the stored timestamp format."""

    return _iso(datetime.now(timezone.utc))


def _iso(moment: datetime) -> str:
    """Format a datetime as UTC ISO text. One format everywhere, so text
    comparison of two stored timestamps is also a time comparison."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


# --- devices ---------------------------------------------------------------


def create_device(label: str, token_hash: bytes) -> str:
    """Register a device (or return the existing one for this token)."""

    device_id = uuid.uuid4().hex
    with _db() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO devices (id, label, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (device_id, label, token_hash, _now()),
        )
    row = _db().execute("SELECT id FROM devices WHERE token_hash = ?", (token_hash,)).fetchone()
    return row["id"]


def device_labels() -> List[str]:
    """Labels of every paired device."""

    return [row["label"] for row in _db().execute("SELECT label FROM devices").fetchall()]


def get_device_id(token_hash: bytes) -> Optional[str]:
    """Look up the device paired with a token hash; None if unknown."""

    with _db() as connection:
        row = connection.execute(
            "UPDATE devices SET last_seen_at = ? WHERE token_hash = ? RETURNING id",
            (_now(), token_hash),
        ).fetchone()
    return row["id"] if row else None


# --- indexing --------------------------------------------------------------


def content_hash(extracted_text: str) -> str:
    """Fingerprint of a page's text, used to skip re-indexing unchanged pages."""

    return hashlib.sha256(extracted_text.encode()).hexdigest()


def find_page(url: str) -> Optional[Dict[str, Any]]:
    """Return the id and content hash of an indexed URL, or None."""

    row = _db().execute("SELECT id, content_hash FROM pages WHERE url = ?", (url,)).fetchone()
    return dict(row) if row else None


def is_blocked(domain: str) -> bool:
    """Whether the user asked to forget this site."""

    row = _db().execute("SELECT 1 FROM blocked_sites WHERE domain = ?", (domain,)).fetchone()
    return row is not None


def read_score(visit: Dict[str, Any]) -> float:
    """How thoroughly a visit read the page, from 0 to 1."""

    dwell = min((visit.get("dwell_ms") or 0) / config.READ_SCORE_FULL_DWELL_MS, 1.0)
    scroll = (visit.get("scroll_depth_pct") or 0) / 100.0
    return dwell * config.READ_SCORE_DWELL_WEIGHT + scroll * config.READ_SCORE_SCROLL_WEIGHT


def record_visit(page_id: str, device_id: Optional[str], raw_url: str, visit: Dict[str, Any]) -> None:
    """Record another visit to an already-indexed, unchanged page."""

    with _db() as connection:
        _insert_visit(connection, page_id, device_id, raw_url, visit)


def save_page(
    *,
    device_id: Optional[str],
    domain: str,
    url: str,
    raw_url: str,
    title: Optional[str],
    lang: Optional[str],
    extracted_text: str,
    chunks: Sequence[ChunkRow],
    visit: Dict[str, Any],
) -> str:
    """Store a new or changed page with its chunks and visit in ONE
    transaction: a half-indexed page must never be committed.

    Returns:
        The page id.
    """

    global _vector_index
    now = _now()
    existing = find_page(url)
    page_id = existing["id"] if existing else uuid.uuid4().hex

    with _db() as connection:
        if existing:
            # the page changed: replace its text and chunks
            _delete_chunks(connection, page_id)
            connection.execute(
                """
                UPDATE pages SET title = ?, lang = ?, extracted_text = ?, content_hash = ?,
                                 word_count = ?, indexed_at = ?
                 WHERE id = ?
                """,
                (title, lang, extracted_text, content_hash(extracted_text),
                 len(extracted_text.split()), now, page_id),
            )
        else:
            started_at = _iso(visit["started_at"])
            connection.execute(
                """
                INSERT INTO pages (id, url, domain, title, lang, extracted_text, content_hash,
                                   word_count, first_visited_at, last_visited_at, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (page_id, url, domain, title, lang, extracted_text, content_hash(extracted_text),
                 len(extracted_text.split()), started_at, started_at, now),
            )

        for chunk in chunks:
            cursor = connection.execute(
                """
                INSERT INTO chunks (page_id, ordinal, heading_path, text, token_count,
                                    char_start, char_end, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (page_id, chunk.ordinal, json.dumps(chunk.heading_path), chunk.text,
                 chunk.token_count, chunk.char_start, chunk.char_end,
                 np.asarray(chunk.embedding, dtype=np.float32).tobytes()),
            )
            connection.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                (cursor.lastrowid, chunk.text),
            )

        _insert_visit(connection, page_id, device_id, raw_url, visit)

    _vector_index = None
    return page_id


def _insert_visit(
    connection: sqlite3.Connection,
    page_id: str,
    device_id: Optional[str],
    raw_url: str,
    visit: Dict[str, Any],
) -> None:
    """Add a visit row and fold it into the page's recency and read score."""

    started_at = _iso(visit["started_at"])
    connection.execute(
        """
        INSERT INTO visits (page_id, device_id, raw_url, started_at, dwell_ms,
                            scroll_depth_pct, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (page_id, device_id, raw_url, started_at, visit.get("dwell_ms") or 0,
         visit.get("scroll_depth_pct"), visit.get("source") or "navigation"),
    )
    connection.execute(
        """
        UPDATE pages SET last_visited_at = max(last_visited_at, ?),
                         read_score      = max(read_score, ?)
         WHERE id = ?
        """,
        (started_at, read_score(visit), page_id),
    )


def _delete_chunks(connection: sqlite3.Connection, page_id: str) -> None:
    """Remove a page's chunks from both the table and the keyword index."""

    connection.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE page_id = ?)",
        (page_id,),
    )
    connection.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))


# --- job queue -------------------------------------------------------------


def enqueue(idempotency_key: str, payload: Dict[str, Any]) -> bool:
    """Record a page for background indexing.

    Returns:
        False if this key was already queued. The extension may retry after a
        response it never saw; the UNIQUE key makes that safe.
    """

    with _db() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO jobs (idempotency_key, payload, created_at) VALUES (?, ?, ?)",
            (idempotency_key, json.dumps(payload), _now()),
        )
    return cursor.rowcount == 1


def claim_jobs(limit: int) -> List[Dict[str, Any]]:
    """Take the oldest pending jobs and mark them running."""

    with _db() as connection:
        rows = connection.execute(
            "SELECT id, payload, attempts FROM jobs WHERE status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE jobs SET status = 'running', claimed_at = ?, attempts = attempts + 1 WHERE id = ?",
                (_now(), row["id"]),
            )
    return [
        {"id": row["id"], "payload": json.loads(row["payload"]), "attempts": row["attempts"] + 1}
        for row in rows
    ]


def finish_job(job_id: int, error: Optional[str] = None) -> None:
    """Success -> done (payload emptied). Failure -> retried until
    ``config.JOB_MAX_ATTEMPTS``, then parked as failed with the error."""

    with _db() as connection:
        if error is None:
            connection.execute(
                "UPDATE jobs SET status = 'done', payload = '{}', finished_at = ?, last_error = NULL WHERE id = ?",
                (_now(), job_id),
            )
        else:
            connection.execute(
                """
                UPDATE jobs
                   SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
                       finished_at = CASE WHEN attempts >= ? THEN ? END,
                       last_error = ?
                 WHERE id = ?
                """,
                (config.JOB_MAX_ATTEMPTS, config.JOB_MAX_ATTEMPTS, _now(), error[:2000], job_id),
            )


# --- search ----------------------------------------------------------------


def _vectors() -> Tuple[np.ndarray, np.ndarray]:
    """Chunk ids and their unit-length embeddings, loaded once per write."""

    global _vector_index
    if _vector_index is None:
        rows = _db().execute("SELECT id, embedding FROM chunks ORDER BY id").fetchall()
        ids = np.array([row["id"] for row in rows], dtype=np.int64)
        if rows:
            matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
            matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
        else:
            matrix = np.zeros((0, 0), dtype=np.float32)
        _vector_index = (ids, matrix)
    return _vector_index


def _similarities(embedding: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    """Cosine similarity of one embedding against every stored chunk."""

    ids, matrix = _vectors()
    if ids.size == 0:
        return ids, np.zeros(0, dtype=np.float32)
    query = np.asarray(embedding, dtype=np.float32)
    return ids, matrix @ (query / np.linalg.norm(query))


def _eligible_chunk_ids(
    since: Optional[datetime],
    until: Optional[datetime],
    domains: Optional[Iterable[str]],
) -> Optional[Set[int]]:
    """Chunk ids passing the date/site filters; None means "no filters"."""

    domains = list(domains or [])
    if since is None and until is None and not domains:
        return None

    conditions, parameters = [], []
    if since is not None:
        conditions.append("p.last_visited_at >= ?")
        parameters.append(_iso(since))
    if until is not None:
        conditions.append("p.last_visited_at < ?")
        parameters.append(_iso(until))
    if domains:
        conditions.append(f"p.domain IN ({', '.join('?' * len(domains))})")
        parameters.extend(domains)

    rows = _db().execute(
        f"SELECT c.id FROM chunks c JOIN pages p ON p.id = c.page_id WHERE {' AND '.join(conditions)}",
        parameters,
    ).fetchall()
    return {row["id"] for row in rows}


def _keyword_ranked(question: str, eligible: Optional[Set[int]]) -> List[int]:
    """Chunk ids ranked by keyword relevance (BM25), best first."""

    words = [w for w in _WORD_RE.findall(question.lower()) if len(w) > 1 and w not in _STOPWORDS]
    if not words:
        return []

    # any word may match; BM25 ranks chunks matching more (and rarer) words higher
    match = " OR ".join(f'"{word}"' for word in dict.fromkeys(words))
    rows = _db().execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, config.RETRIEVAL_CANDIDATES * 4),
    ).fetchall()
    ranked = [row["rowid"] for row in rows if eligible is None or row["rowid"] in eligible]
    return ranked[: config.RETRIEVAL_CANDIDATES]


def _chunk_rows(chunk_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """Fetch chunks with their page details, keyed by chunk id."""

    if not chunk_ids:
        return {}
    rows = _db().execute(
        f"""
        SELECT c.id, c.page_id, c.text, c.heading_path, c.char_start, c.char_end,
               p.title, p.url, p.domain, p.last_visited_at, p.read_score
        FROM chunks c JOIN pages p ON p.id = c.page_id
        WHERE c.id IN ({', '.join('?' * len(chunk_ids))})
        """,
        list(chunk_ids),
    ).fetchall()

    result = {}
    for row in rows:
        item = dict(row)
        item["heading_path"] = json.loads(item["heading_path"])
        item["last_visited_at"] = datetime.fromisoformat(item["last_visited_at"])
        result[item["id"]] = item
    return result


def hybrid_search(
    embedding: Sequence[float],
    question: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    domains: Optional[Iterable[str]] = None,
    limit: int = config.RETRIEVAL_TOP_K,
    use_vectors: bool = True,
    use_keywords: bool = True,
) -> List[Dict[str, Any]]:
    """Vector + keyword retrieval fused with Reciprocal Rank Fusion.

    Each ranking contributes ``1 / (RRF_K + rank)`` per chunk; chunks found by
    both rise to the top. Well-read pages get a small boost.

    Args:
        embedding: The query embedding.
        question: The topical query text, for keyword search.
        since: Only pages visited at or after this time.
        until: Only pages visited before this time.
        domains: Only pages from these sites.
        limit: How many chunks to return.
        use_vectors: Include the vector ranking (off for keyword-only evaluation).
        use_keywords: Include the keyword ranking (off for vector-only evaluation).

    Returns:
        Chunks with their page details, best first.
    """

    eligible = _eligible_chunk_ids(since, until, domains)
    if eligible is not None and not eligible:
        return []

    rankings: List[List[int]] = []

    # how close each chunk is to the question, kept on every returned row so
    # callers can tell a strong match from "the least bad of an unrelated lot"
    ids, similarities = _similarities(embedding)
    similarity_of = dict(zip(ids.tolist(), similarities.tolist()))

    if use_vectors:
        if eligible is not None and ids.size:
            similarities = np.where(np.isin(ids, list(eligible)), similarities, -np.inf)
        order = np.argsort(-similarities)[: config.RETRIEVAL_CANDIDATES]
        rankings.append([int(ids[i]) for i in order if np.isfinite(similarities[i])])

    if use_keywords:
        rankings.append(_keyword_ranked(question, eligible))

    scores: Dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (config.RRF_K + rank)

    rows = _chunk_rows(list(scores))
    for chunk_id, row in rows.items():
        row["score"] = scores[chunk_id] + config.READ_SCORE_RANK_BOOST * row["read_score"]
        row["similarity"] = float(similarity_of.get(chunk_id, 0.0))

    return sorted(rows.values(), key=lambda row: -row["score"])[:limit]


def related_pages(
    embedding: Sequence[float],
    *,
    exclude_url: str,
    min_similarity: float,
    limit: int,
    candidate_chunks: int,
) -> List[Dict[str, Any]]:
    """Pages whose best chunk is close to the embedding, excluding one URL.

    Cosine similarity is an absolute score (unlike fused ranks), so the cutoff
    can mean "nothing related" instead of always returning the top few.
    """

    ids, similarities = _similarities(embedding)
    order = np.argsort(-similarities)[:candidate_chunks]
    nearest = {int(ids[i]): float(similarities[i]) for i in order}
    rows = _chunk_rows(list(nearest))

    best_per_page: Dict[str, Dict[str, Any]] = {}
    for chunk_id, row in rows.items():
        row["similarity"] = nearest[chunk_id]
        if row["url"] == exclude_url or row["similarity"] < min_similarity:
            continue
        current = best_per_page.get(row["page_id"])
        if current is None or row["similarity"] > current["similarity"]:
            best_per_page[row["page_id"]] = row

    return sorted(best_per_page.values(), key=lambda row: -row["similarity"])[:limit]


def known_domains(domains: Iterable[str]) -> List[str]:
    """Indexed sites matching the given domains, subdomains included.

    "wikipedia.org" matches pages stored under "en.wikipedia.org": people name
    a site, not the exact host it is served from.
    """

    wanted = [domain.lower().removeprefix("www.") for domain in domains]
    if not wanted:
        return []
    stored = [row["domain"] for row in _db().execute("SELECT DISTINCT domain FROM pages")]
    return [
        domain
        for domain in stored
        if any(domain == name or domain.endswith("." + name) for name in wanted)
    ]


# --- pages panel -----------------------------------------------------------


def status_counts() -> Dict[str, int]:
    """Queue depth and corpus size for the side panel header."""

    row = _db().execute(
        """
        SELECT
          (SELECT count(*) FROM jobs WHERE status IN ('pending', 'running')) AS pending_jobs,
          (SELECT count(*) FROM jobs WHERE status = 'failed')                AS failed_jobs,
          (SELECT count(*) FROM pages)  AS pages,
          (SELECT count(*) FROM chunks) AS chunks
        """
    ).fetchone()
    return dict(row)


def page_text_for_url(url: str) -> Optional[str]:
    """Stored text of an already-indexed URL (fallback for the current page)."""

    row = _db().execute("SELECT extracted_text FROM pages WHERE url = ?", (url,)).fetchone()
    return row["extracted_text"] if row else None


def list_pages(*, limit: int = 50, offset: int = 0, q: Optional[str] = None) -> List[Dict[str, Any]]:
    """Indexed pages, most recently visited first, optionally filtered by a
    substring of the title or URL."""

    pattern = f"%{q}%" if q else None
    rows = _db().execute(
        """
        SELECT id, url, domain, title, word_count, read_score, last_visited_at, indexed_at
        FROM pages
        WHERE ? IS NULL OR title LIKE ? OR url LIKE ?
        ORDER BY last_visited_at DESC
        LIMIT ? OFFSET ?
        """,
        (pattern, pattern, pattern, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_page(page_id: str) -> bool:
    """Delete one page with its chunks and visits. False if it did not exist."""

    global _vector_index
    with _db() as connection:
        _delete_chunks(connection, page_id)
        cursor = connection.execute("DELETE FROM pages WHERE id = ?", (page_id,))
    _vector_index = None
    return cursor.rowcount == 1


def forget_site(domain: str) -> int:
    """Block a domain from future indexing and delete everything indexed from it.

    Returns:
        The number of pages deleted.
    """

    global _vector_index
    with _db() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO blocked_sites (domain, blocked_at) VALUES (?, ?)",
            (domain, _now()),
        )
        connection.execute(
            """
            DELETE FROM chunks_fts WHERE rowid IN (
              SELECT c.id FROM chunks c JOIN pages p ON p.id = c.page_id WHERE p.domain = ?
            )
            """,
            (domain,),
        )
        cursor = connection.execute("DELETE FROM pages WHERE domain = ?", (domain,))
    _vector_index = None
    return cursor.rowcount


def unblock_site(domain: str) -> None:
    """Allow a previously forgotten site to be indexed again."""

    with _db() as connection:
        connection.execute("DELETE FROM blocked_sites WHERE domain = ?", (domain,))


# --- query log -------------------------------------------------------------


def log_query(
    *,
    device_id: Optional[str],
    question: str,
    parsed_filter: Optional[Dict[str, Any]],
    chunk_ids: List[int],
    answer: Optional[str],
    abstained: bool,
    embed_model: str,
    llm_model: str,
    latency_ms: int,
) -> None:
    """Record a question and what was retrieved for it."""

    with _db() as connection:
        connection.execute(
            """
            INSERT INTO queries (device_id, question, parsed_filter, chunk_ids, answer,
                                 abstained, embed_model, llm_model, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (device_id, question, json.dumps(parsed_filter), json.dumps(chunk_ids), answer,
             int(abstained), embed_model, llm_model, latency_ms, _now()),
        )
