"""Open-page passage selection. Uses the real local embedder (no network).

LivePageService does not chunk or embed anything itself, so the tests that
need real passages run those stages first — the same way the orchestrator does.
"""

from types import SimpleNamespace

from schemas import CurrentPageIn
from services.chunking_service import ChunkingService
from services.embedding_service import EmbeddingService
from services.live_page_service import LivePageService, select_passages, text_to_html


def chunk(text: str):
    return SimpleNamespace(text=text, heading_path=[])


def section(title: str, sentence: str, repeats: int = 25) -> str:
    return f"<h2>{title}</h2><p>{' '.join([sentence] * repeats)}</p>"


LONG_ARTICLE = (
    "<article><h1>A long guide</h1><p>This guide covers gardening, databases and sailing.</p>"
    + section("Tomatoes", "Tomato plants need full sun and regular watering to fruit well.")
    + section("Indexes", "A database index speeds up queries by avoiding full table scans.")
    + section("Knots", "A bowline knot forms a fixed loop at the end of a sailing rope.")
    + section("Compost", "Compost adds nutrients back into garden soil over the season.")
    + section("Sails", "Trimming the mainsail changes how the boat points into the wind.")
    + "</article>"
)
URL = "https://guide.example/long"


def test_short_page_is_passed_whole():
    chunks = [chunk("a" * 100), chunk("b" * 100)]
    assert select_passages(chunks, [0.1, 0.9], char_budget=1000) == chunks


def test_long_page_keeps_opening_then_most_relevant_in_page_order():
    chunks = [chunk("intro " * 20), chunk("x " * 200), chunk("y " * 200), chunk("z " * 200)]
    picked = select_passages(chunks, [0.0, 0.2, 0.9, 0.5], char_budget=950)
    assert picked == [chunks[0], chunks[2], chunks[3]], "opening + best two, in page order"


def test_best_match_is_never_squeezed_out_by_the_opening():
    chunks = [chunk("o" * 600), chunk("x" * 600), chunk("best" * 150)]
    picked = select_passages(chunks, [0.1, 0.2, 0.9], char_budget=700)
    assert picked == [chunks[2]], "the answer beats the opening when both cannot fit"

    huge = [chunk("o" * 100), chunk("best" * 1000)]
    assert select_passages(huge, [0.1, 0.9], char_budget=500) == [huge[1]]


def test_plain_text_becomes_paragraphs_and_long_captions_are_wrapped():
    html = text_to_html("First paragraph.\n\nSecond <b>one</b>.")
    assert html == "<p>First paragraph.</p><p>Second &lt;b&gt;one&lt;/b&gt;.</p>"

    captions = "word " * 1200  # 6000 chars with no sentence punctuation
    assert text_to_html(captions).count("<p>") >= 5


def test_page_content_preference_order():
    service = LivePageService()
    page = CurrentPageIn(url=URL, html="<p>from html</p>", text="from text")

    assert "the transcript" in service.article_html(page, transcript="the transcript")
    assert service.article_html(page) == "<p>from html</p>"
    assert service.article_html(CurrentPageIn(url=URL, text="from text")) == "<p>from text</p>"
    assert service.article_html(CurrentPageIn(url=URL), stored_text="kept") == "<p>kept</p>"
    assert service.article_html(CurrentPageIn(url=URL)) is None


async def test_relevant_passage_of_a_long_page_is_selected():
    chunking, embedding, service = ChunkingService(), EmbeddingService(), LivePageService()

    _, chunks = chunking.chunk(LONG_ARTICLE)
    vectors = await embedding.embed_passages([c.text for c in chunks])
    chunks, matrix = service.cache_passages(URL, LONG_ARTICLE, chunks, vectors)
    query = await embedding.embed_query("how do database indexes make queries faster")

    picked = service.select(chunks, matrix, query, char_budget=4000)

    texts = " ".join(c.text for c in picked)
    assert "database index" in texts, "the passage that answers the question is included"
    assert "This guide covers" in picked[0].text, "the opening passage is kept when it fits"
    assert sum(len(c.text) for c in picked) <= 4000
    assert len(picked) < len(chunks), "a long page is narrowed down, not passed whole"


def test_passages_are_cached_per_page_content():
    service = LivePageService()
    assert service.cached_passages(URL, "<p>v1</p>") is None

    chunks, matrix = service.cache_passages(URL, "<p>v1</p>", [chunk("v1")], [[1.0, 0.0]])
    hit = service.cached_passages(URL, "<p>v1</p>")
    assert hit is not None and hit[0] is chunks and hit[1] is matrix
    assert service.cached_passages(URL, "<p>v2 — the page changed</p>") is None
    assert service.select([], matrix, [1.0, 0.0]) == []
