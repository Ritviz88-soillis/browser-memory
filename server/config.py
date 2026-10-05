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
# Chat model. LLM_PROVIDER is "groq", "huggingface" or "gemini"; left empty,
# the first provider that has a key in .env is used, in that order.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
HF_MODEL = os.environ.get("HF_MODEL", "meta-llama/Llama-3.3-70B-Instruct")
HF_MAX_NEW_TOKENS = 1024
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

# Open tabs ("live pages"): the page is split into passages and the ones most
# relevant to the question are cited, so a citation can point at an exact spot.
CURRENT_PAGE_TEXT_CAP = 12_000
LIVE_PAGE_HTML_CAP = 600_000
LIVE_PAGE_CHAR_BUDGET = 8_000   # passage text from one open page per question
LIVE_PAGE_CACHE_MAX = 16        # pages whose passages stay embedded in memory
# Comparing several ticked tabs: the budget is shared equally between them
MAX_COMPARE_TABS = 5
COMPARE_TABS_CHAR_BUDGET = 14_000
UNREADABLE_TABS_TEXT = "I couldn't read any text from the selected tabs."
SOURCE_SNIPPET_CHARS = 300

# Evidence: the sentences of a cited passage that support the answer, which
# are what gets highlighted most strongly on the page.
EVIDENCE_MIN_SPAN_CHARS = 20
EVIDENCE_MAX_SPANS = 3
EVIDENCE_MIN_SIMILARITY = 0.62       # a sentence this close to a claim supports it
EVIDENCE_FALLBACK_SIMILARITY = 0.50  # if none is, keep the best one above this

# Claim check: a cited sentence must share at least this share of its
# meaningful words with the passage it cites, or its citation is withdrawn.
# Measured on real answers: supported sentences scored 0.82-1.00, sentences
# the model added from its own knowledge 0.14-0.50.
SUPPORT_MIN_WORD_OVERLAP = 0.6
SUPPORT_MIN_WORDS = 3  # fewer meaningful words than this is too little to judge

# When the user is on a page, memory is extra context, not the subject. A
# stored passage is added only if it is at least this similar to the question,
# so unrelated reading does not crowd the prompt.
MEMORY_MIN_SIMILARITY_BESIDE_OPEN_PAGE = 0.55

# What the user sees when the sources hold no answer
PAGE_NOT_COVERED_TEXT = (
    "The page you're on doesn't cover this, and nothing else you've read does either."
)
TABS_NOT_COVERED_TEXT = "None of the selected tabs cover this."

# YouTube transcripts: an hour of speech is ~10k chars
TRANSCRIPT_TEXT_CAP = 24_000
TRANSCRIPT_LANGUAGES = ["en", "en-US", "en-IN", "hi"]
TRANSCRIPT_CACHE_MAX = 200

INGEST_HTML_MAX_CHARS = 2_000_000  # largest page (as article HTML) accepted for indexing

# PDFs: the extension sends the file, the server extracts its text per page
PDF_MAX_BYTES = 20_000_000
PDF_MAX_PAGES = 300

# Background ingest worker (woken as soon as a page arrives; the poll is a fallback)
WORKER_POLL_SECONDS = 2.0
WORKER_BATCH_SIZE = 4
JOB_MAX_ATTEMPTS = 3

# Pages listing
PAGES_MAX_LIMIT = 200
