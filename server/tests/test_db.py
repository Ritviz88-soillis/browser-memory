"""Data-layer tests on a throwaway SQLite file. Embeddings are tiny hand-made
vectors, so these run offline in milliseconds and the expected rankings are
obvious by inspection."""

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

# three "topics" as orthogonal directions
DATABASES = [1.0, 0.0, 0.0]
COOKING = [0.0, 1.0, 0.0]
MUSIC = [0.0, 0.0, 1.0]


def save(memory, url, domain, text, embedding, *, when=NOW, dwell_ms=0, scroll=None, title=None):
    return memory.save_page(
        device_id=None,
        domain=domain,
        url=url,
        raw_url=url,
        title=title or url,
        lang="en",
        extracted_text=text,
        chunks=[
            memory.ChunkRow(
                ordinal=0,
                heading_path=["Intro"],
                text=text,
                token_count=len(text.split()),
                char_start=0,
                char_end=len(text),
                embedding=embedding,
            )
        ],
        visit={"started_at": when, "dwell_ms": dwell_ms, "scroll_depth_pct": scroll, "source": "navigation"},
    )


def seed(memory):
    save(memory, "https://pg.dev/mvcc", "pg.dev", "Postgres uses MVCC so readers never block writers.", DATABASES)
    save(memory, "https://food.com/bread", "food.com", "Sourdough bread needs flour, water and salt.", COOKING)
    save(memory, "https://tunes.fm/heart", "tunes.fm", "Heart is a rock band from Seattle.", MUSIC,
         when=NOW - timedelta(days=30))


def test_vector_search_ranks_the_matching_topic_first(memory):
    seed(memory)
    rows = memory.hybrid_search(DATABASES, "zzz", use_keywords=False)
    assert rows[0]["url"] == "https://pg.dev/mvcc"
    assert rows[0]["heading_path"] == ["Intro"]
    assert isinstance(rows[0]["last_visited_at"], datetime)


def test_keyword_search_finds_exact_words_and_stems(memory):
    seed(memory)
    rows = memory.hybrid_search(COOKING, "what bands are from seattle", use_vectors=False)
    assert [r["url"] for r in rows] == ["https://tunes.fm/heart"], "'bands' must match 'band'"


def test_hybrid_puts_a_chunk_found_by_both_rankings_on_top(memory):
    seed(memory)
    rows = memory.hybrid_search(DATABASES, "postgres mvcc")
    assert rows[0]["url"] == "https://pg.dev/mvcc"


def test_date_and_site_filters(memory):
    seed(memory)
    recent = memory.hybrid_search(MUSIC, "band", since=NOW - timedelta(days=7))
    assert all(r["url"] != "https://tunes.fm/heart" for r in recent), "30-day-old page filtered out"

    old = memory.hybrid_search(MUSIC, "band", until=NOW - timedelta(days=7))
    assert [r["url"] for r in old] == ["https://tunes.fm/heart"]

    one_site = memory.hybrid_search(DATABASES, "anything", domains=["food.com"])
    assert [r["domain"] for r in one_site] == ["food.com"]

    assert memory.known_domains(["food.com", "never-visited.com"]) == ["food.com"]


def test_a_site_name_matches_its_subdomains(memory):
    save(memory, "https://en.wikipedia.org/wiki/SQLite", "en.wikipedia.org", "SQLite is a database engine.", DATABASES)
    save(memory, "https://notwikipedia.org/x", "notwikipedia.org", "An unrelated site.", COOKING)

    assert memory.known_domains(["wikipedia.org"]) == ["en.wikipedia.org"]
    assert memory.known_domains(["WWW.Wikipedia.org"]) == ["en.wikipedia.org"]
    assert memory.known_domains(["pedia.org"]) == [], "a suffix of a name is not a subdomain"

    rows = memory.hybrid_search(DATABASES, "database", domains=memory.known_domains(["wikipedia.org"]))
    assert [r["domain"] for r in rows] == ["en.wikipedia.org"]


def test_unchanged_page_is_detected_and_changed_page_replaces_chunks(memory):
    text = "Postgres uses MVCC so readers never block writers."
    page_id = save(memory, "https://pg.dev/mvcc", "pg.dev", text, DATABASES)

    found = memory.find_page("https://pg.dev/mvcc")
    assert found["id"] == page_id and found["content_hash"] == memory.content_hash(text)

    same_id = save(memory, "https://pg.dev/mvcc", "pg.dev", "Now the page is about bread and flour.", COOKING)
    assert same_id == page_id
    assert memory.status_counts()["pages"] == 1 and memory.status_counts()["chunks"] == 1
    assert memory.hybrid_search(COOKING, "mvcc", use_vectors=False) == [], "old text left the keyword index"
    assert memory.hybrid_search(COOKING, "bread", use_vectors=False)[0]["page_id"] == page_id


def test_well_read_page_outranks_a_skimmed_one(memory):
    text = "Notes about vector indexes."
    save(memory, "https://a.dev/skimmed", "a.dev", text, DATABASES, dwell_ms=5_000, scroll=10)
    save(memory, "https://b.dev/studied", "b.dev", text, DATABASES, dwell_ms=300_000, scroll=100)
    rows = memory.hybrid_search(DATABASES, "vector indexes")
    assert rows[0]["url"] == "https://b.dev/studied"
    assert rows[0]["read_score"] == 1.0


def test_related_pages_respects_cutoff_and_excludes_the_page_itself(memory):
    seed(memory)
    related = memory.related_pages(
        DATABASES, exclude_url="https://elsewhere.com/x", min_similarity=0.68, limit=3, candidate_chunks=40
    )
    assert [r["url"] for r in related] == ["https://pg.dev/mvcc"], "unrelated topics stay below the cutoff"

    own = memory.related_pages(
        DATABASES, exclude_url="https://pg.dev/mvcc", min_similarity=0.68, limit=3, candidate_chunks=40
    )
    assert own == []


def test_forget_site_deletes_and_blocks(memory):
    seed(memory)
    assert memory.forget_site("food.com") == 1
    assert memory.is_blocked("food.com")
    assert memory.status_counts()["pages"] == 2
    assert memory.hybrid_search(COOKING, "bread", use_vectors=False) == []
    memory.unblock_site("food.com")
    assert not memory.is_blocked("food.com")


def test_delete_page(memory):
    page_id = save(memory, "https://pg.dev/mvcc", "pg.dev", "Postgres MVCC.", DATABASES)
    assert memory.delete_page(page_id) is True
    assert memory.delete_page(page_id) is False
    assert memory.status_counts() == {"pending_jobs": 0, "failed_jobs": 0, "pages": 0, "chunks": 0}


def test_job_queue_is_idempotent_and_retries_then_parks(memory):
    assert memory.enqueue("key-12345", {"url": "https://x.dev"}) is True
    assert memory.enqueue("key-12345", {"url": "https://x.dev"}) is False

    for attempt in (1, 2, 3):
        jobs = memory.claim_jobs(4)
        assert len(jobs) == 1 and jobs[0]["attempts"] == attempt
        assert jobs[0]["payload"] == {"url": "https://x.dev"}
        memory.finish_job(jobs[0]["id"], error="boom")

    assert memory.claim_jobs(4) == [], "parked as failed after the last attempt"
    assert memory.status_counts()["failed_jobs"] == 1


def test_devices(memory):
    device_id = memory.create_device("laptop", b"hash-one")
    assert memory.get_device_id(b"hash-one") == device_id
    assert memory.get_device_id(b"unknown") is None
    assert memory.create_device("laptop again", b"hash-one") == device_id
