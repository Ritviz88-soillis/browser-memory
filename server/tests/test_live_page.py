"""Open-page passage selection. Uses the real local embedder (no network)."""

from types import SimpleNamespace

from schemas import CurrentPageIn
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


async def test_relevant_passage_of_a_long_page_is_selected():
    service = LivePageService()
    page = CurrentPageIn(url="https://guide.example/long", title="A long guide", html=LONG_ARTICLE, tab_id=3)

    everything = await service.passages(page, "anything", char_budget=1_000_000)
    picked = await service.passages(page, "how do database indexes make queries faster", char_budget=4000)

    texts = " ".join(c.text for c in picked)
    assert "database index" in texts, "the passage that answers the question is included"
    assert "This guide covers" in picked[0].text, "the opening passage is kept when it fits"
    assert sum(len(c.text) for c in picked) <= 4000
    assert len(picked) < len(everything), "a long page is narrowed down, not passed whole"


async def test_page_passages_are_cached_between_questions():
    service = LivePageService()
    page = CurrentPageIn(url="https://guide.example/long", html=LONG_ARTICLE)
    await service.passages(page, "tomatoes")
    await service.passages(page, "sailing knots")
    assert len(service._cache) == 1


async def test_page_without_readable_content_yields_no_passages(memory):
    service = LivePageService()
    page = CurrentPageIn(url="https://nothing.example/empty")
    assert await service.passages(page, "anything") == []
