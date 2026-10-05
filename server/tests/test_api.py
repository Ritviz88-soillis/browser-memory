"""Full-lifecycle test through the real app: auth, idempotent enqueue, worker
indexing, grounded ask, proactive recall, query logging, forget.

Runs against a throwaway database (see conftest). Only the /ask step needs the
network, for the LLM — so the test is skipped when no LLM key is configured.
"""

import asyncio
import hashlib
import os
import secrets
import uuid

import httpx
import pytest

import config  # noqa: F401  (loads .env so the key check below sees it)

pytestmark = pytest.mark.skipif(
    not (os.environ.get("GROQ_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
    reason="no LLM key configured",
)

TEST_DOMAIN = "testpage-example.dev"


@pytest.fixture
async def client(memory):
    from main import app

    memory.close()  # the app opens the (now empty) database itself
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


async def test_full_lifecycle(client):
    import db

    marker = uuid.uuid4().hex[:8]

    # unauthenticated requests are rejected
    r = await client.post("/ask", json={"question": "hi"})
    assert r.status_code == 401

    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True

    # pairing: a token is issued only to a browser extension, never to a web
    # page or a bare request, and only to the first extension that asks
    assert (await client.post("/pair")).status_code == 403
    r = await client.post("/pair", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = await client.post("/pair", headers={"Origin": "chrome-extension://myextensionid"})
    assert r.status_code == 200
    paired = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/status", headers=paired)).status_code == 200
    r = await client.post("/pair", headers={"Origin": "chrome-extension://myextensionid"})
    assert r.status_code == 200, "the same extension may pair again (e.g. after a reinstall)"
    r = await client.post("/pair", headers={"Origin": "chrome-extension://anotherextension"})
    assert r.status_code == 403, "a second, different extension is refused"

    # a token made by hand with scripts/new_device.py keeps working too
    token = secrets.token_urlsafe(24)
    db.create_device("pytest", hashlib.sha256(token.encode()).digest())
    auth = {"Authorization": f"Bearer {token}"}

    # ingest a page whose content carries a unique marker
    html = f"""
    <html><body><article>
      <h1>Zebra quantum notes {marker}</h1>
      <p>The zebra quantum {marker} experiment measured decoherence of striped
      qubits at room temperature and found a 42 millisecond coherence window.</p>
    </article></body></html>
    """
    body = {
        "idempotency_key": f"pytest-{marker}",
        "url": f"https://{TEST_DOMAIN}/notes/{marker}?utm_source=test",
        "title": f"Zebra quantum notes {marker}",
        "html": html,
        "visit": {"started_at": "2026-07-29T10:00:00+00:00", "dwell_ms": 30000, "scroll_depth_pct": 80},
    }
    r = await client.post("/ingest", json=body, headers=auth)
    assert r.status_code == 202 and r.json() == {"queued": True, "duplicate": False}

    # idempotency: same key again is flagged duplicate
    r = await client.post("/ingest", json=body, headers=auth)
    assert r.json()["duplicate"] is True

    # the background worker indexes it
    for _ in range(100):
        r = await client.get("/pages", params={"q": marker}, headers=auth)
        if r.json():
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("worker did not index the page in time")
    page = r.json()[0]
    assert page["domain"] == TEST_DOMAIN
    assert "utm_source" not in page["url"], "tracking parameters are stripped"

    # the same content under a new key is a revisit, not a second copy
    r = await client.post("/ingest", json=body | {"idempotency_key": f"pytest-again-{marker}"}, headers=auth)
    assert r.json()["duplicate"] is False
    await asyncio.sleep(0.5)
    r = await client.get("/status", headers=auth)
    assert r.json() == {"pending_jobs": 0, "failed_jobs": 0, "pages": 1, "chunks": 1}

    # grounded answer with a citation to the ingested page
    r = await client.post(
        "/ask",
        json={"question": f"what did the zebra quantum {marker} experiment measure?",
              "no_filters": True},
        headers=auth,
    )
    assert r.status_code == 200
    out = r.json()
    # assert on OUR pipeline (grounding, citation, sources) — never on the
    # LLM's exact wording, which is not ours to test
    assert out["abstained"] is False
    assert "[1]" in out["answer"], "answer must carry a validated citation"
    assert any(s["domain"] == TEST_DOMAIN for s in out["sources"])

    # a question about the open page cites an exact passage of it, tagged
    # with the tab it came from so the extension can highlight it there
    r = await client.post(
        "/ask",
        json={
            "question": "according to this page, how tall is the Glimmer tower?",
            "no_filters": True,
            "current_page": {
                "url": "https://live.example/tower",
                "title": "Glimmer tower",
                "tab_id": 7,
                "html": "<article><h1>Glimmer tower</h1><p>The Glimmer tower in Zarnville "
                        "is 412 metres tall and opened in 1987.</p></article>",
            },
        },
        headers=auth,
    )
    assert r.status_code == 200
    live = [s for s in r.json()["sources"] if s["live"]]
    assert live, "the answer must cite the open page"
    assert live[0]["tab_id"] == 7 and "412 metres" in live[0]["passage"]

    # comparing ticked tabs: the answer draws on each tab and on nothing else,
    # even though memory holds a page (the zebra notes) that could match
    def tab(tab_id: int, name: str, price: str) -> dict:
        return {
            "url": f"https://shop.example/{name.lower()}",
            "title": f"{name} laptop",
            "tab_id": tab_id,
            "html": f"<article><h1>{name} laptop</h1><p>The {name} laptop costs {price} rupees, "
                    f"weighs 1.4 kg and its battery lasts 11 hours.</p></article>",
        }

    r = await client.post(
        "/ask",
        json={
            "question": "compare the price of these laptops, and mention the zebra quantum experiment",
            "tabs": [tab(11, "Aurora", "61,000"), tab(12, "Borealis", "74,500")],
        },
        headers=auth,
    )
    assert r.status_code == 200
    compared = r.json()
    assert {s["tab_id"] for s in compared["sources"]} == {11, 12}, "both tabs are cited"
    assert all(s["live"] for s in compared["sources"]), "memory is not searched when tabs are ticked"
    assert compared["filters"]["semantic_query"].startswith("compare the price")

    # ticked tabs with nothing readable get a clear message, not a guess
    r = await client.post(
        "/ask",
        json={"question": "summarise these", "tabs": [{"url": "https://empty.example/a", "tab_id": 13}]},
        headers=auth,
    )
    assert r.json()["abstained"] is True and "selected tabs" in r.json()["answer"]

    # proactive recall: a different page on the same topic surfaces the ingested one
    topic = (
        f"The zebra quantum {marker} experiment measured decoherence of striped "
        "qubits at room temperature."
    )
    r = await client.post(
        "/related",
        json={"url": "https://elsewhere.example/blog/qubits", "title": "Striped qubits", "text": topic},
        headers=auth,
    )
    assert r.status_code == 200
    related = r.json()["pages"]
    assert [p["domain"] for p in related] == [TEST_DOMAIN]
    assert related[0]["similarity"] >= 0.68

    # a page is never reported as related to itself
    r = await client.post(
        "/related", json={"url": body["url"], "title": body["title"], "text": topic}, headers=auth
    )
    assert r.json()["pages"] == []

    # an unrelated page surfaces nothing
    r = await client.post(
        "/related",
        json={
            "url": "https://elsewhere.example/recipes/bread",
            "title": "Sourdough bread recipe",
            "text": "Mix flour, water and salt. Fold the dough every thirty minutes and bake.",
        },
        headers=auth,
    )
    assert r.json()["pages"] == []

    # the question was logged for the eval harness
    logged = db._db().execute(
        "SELECT count(*) FROM queries WHERE question LIKE ?", (f"%{marker}%",)
    ).fetchone()[0]
    assert logged == 1

    # forget the site: its page is gone and it can't be indexed again
    r = await client.delete(f"/sites/{TEST_DOMAIN}", headers=auth)
    assert r.status_code == 200 and r.json()["deleted"] == 1
    r = await client.get("/pages", params={"q": TEST_DOMAIN}, headers=auth)
    assert r.json() == []

    r = await client.post("/ingest", json=body | {"idempotency_key": f"pytest-blocked-{marker}"}, headers=auth)
    assert r.status_code == 202
    await asyncio.sleep(0.5)
    r = await client.get("/status", headers=auth)
    assert r.json()["pages"] == 0 and r.json()["failed_jobs"] == 0
