"""Central configuration for the browser-memory server.

All tunable settings live here so services stay free of hard-coded constants
(mirrors t1's ``app.config`` and naive-rag's ``config.py``).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Loaded here so every entry point (API, scripts, tests) sees the same values.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Database: one SQLite file. Kept outside the project folder by default
# because the project sits in OneDrive, and a file-sync tool copying a live
# database mid-write can corrupt it. Override with MEMORY_DB_PATH in .env.
_DEFAULT_DATABASE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "browser-memory"
DATABASE_PATH = Path(os.environ.get("MEMORY_DB_PATH") or _DEFAULT_DATABASE_DIR / "memory.db")

# Models
EMBEDDING_MODEL = "bge-small-en-v1.5"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
ANSWER_TEMPERATURE = 0.2
QUERY_FILTER_TEMPERATURE = 0.0

# Chunking (token budgets, cl100k_base)
CHUNK_TARGET_TOKENS = 450
CHUNK_MAX_TOKENS = 600
CHUNK_OVERLAP_TOKENS = 60

# Retrieval: vector and keyword rankings fused with Reciprocal Rank Fusion
RETRIEVAL_TOP_K = 6
RETRIEVAL_CANDIDATES = 50  # how many results each ranking contributes
RRF_K = 60
ABSTAIN_TEXT = "You haven't read anything about this."

# Reading depth: time on page saturates at 3 minutes (weight 0.6), scroll
# depth adds up to 0.4. The score nudges well-read pages up the ranking.
READ_SCORE_FULL_DWELL_MS = 180_000
READ_SCORE_DWELL_WEIGHT = 0.6
READ_SCORE_SCROLL_WEIGHT = 0.4
READ_SCORE_RANK_BOOST = 0.005

# Proactive recall ("related to things you read before")
# Cosine similarity cutoff, set from the corpus: related pages score ~0.69+,
# unrelated ones ~0.35-0.55. Below the cutoff the panel shows nothing.
RELATED_MIN_SIMILARITY = 0.68
RELATED_MAX_PAGES = 3
RELATED_CANDIDATE_CHUNKS = 40
RELATED_QUERY_CHARS = 1500  # bge-small reads ~512 tokens; more is truncated anyway
RELATED_SNIPPET_CHARS = 200

# Current page (source [0])
CURRENT_PAGE_TEXT_CAP = 12_000
SOURCE_SNIPPET_CHARS = 300

# YouTube transcripts: an hour of speech is ~10k chars
TRANSCRIPT_TEXT_CAP = 24_000
TRANSCRIPT_LANGUAGES = ["en", "en-US", "en-IN", "hi"]
TRANSCRIPT_CACHE_MAX = 200

# Background ingest worker (woken as soon as a page arrives; the poll is a fallback)
WORKER_POLL_SECONDS = 2.0
WORKER_BATCH_SIZE = 4
JOB_MAX_ATTEMPTS = 3

# Pages listing
PAGES_MAX_LIMIT = 200
