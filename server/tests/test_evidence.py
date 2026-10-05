"""Evidence selection: narrowing a cited passage to the sentences that support
the answer. The semantic tests use the real local embedder (no network)."""

from services.embedding_service import EmbeddingService
from services.evidence_service import EvidenceService

# One passage, as a short page is stored: several sections merged together.
PASSAGE = (
    "Classes and Objects\n\n"
    "Objects are an encapsulation of variables and functions into a single entity. "
    "Objects get their variables and functions from classes. "
    "Classes are essentially a template to create your objects.\n\n"
    "Accessing Object Variables\n\n"
    'To access the variable inside of the newly created object "myobjectx" you would do the following:\n\n'
    "myobjectx.variable\n\n"
    'So for instance the below would output the string "blah".\n\n'
    "init()\n\n"
    "The __init__() function is a special function that is called when the class is being initiated. "
    "It is used for assigning values in a class.\n\n"
    "Exercise\n\n"
    "We have a class defined for vehicles. Create two new vehicles called car1 and car2. "
    "Set car1 to be a red convertible worth $60,000.00 with a name of Fer."
)


def test_spans_are_exact_substrings_in_page_order():
    spans = EvidenceService().spans(PASSAGE)
    assert all(span in PASSAGE for span in spans), "each span must be findable on the page"
    positions = [PASSAGE.index(span) for span in spans]
    assert positions == sorted(positions)
    assert "Objects get their variables and functions from classes." in spans
    assert "Exercise" not in spans, "bare headings are too short to be evidence"


def test_a_dot_inside_a_word_does_not_end_a_sentence():
    spans = EvidenceService().spans("Call myobjectx.variable to read it. The value 3.14 is returned next.")
    assert spans == ["Call myobjectx.variable to read it.", "The value 3.14 is returned next."]


def test_code_block_stays_whole():
    passage = "Here is the class definition for you.\n\nclass MyClass:\n    variable = 'blah'\n\nThat is all there is to it."
    spans = EvidenceService().spans(passage)
    assert "class MyClass:\n    variable = 'blah'" in spans


def test_claims_are_grouped_by_the_source_they_cite():
    answer = (
        "SQLite stores the whole database in a single file [2]. It needs no server process. [2][3]\n"
        "- **PostgreSQL** runs as a separate server [5]\n"
        "This last sentence cites nothing at all."
    )
    claims = EvidenceService().claims(answer)
    assert claims[2] == ["SQLite stores the whole database in a single file.", "It needs no server process."]
    assert claims[3] == ["It needs no server process."]
    assert claims[5] == ["PostgreSQL runs as a separate server"]
    assert set(claims) == {2, 3, 5}


async def test_the_sentence_that_supports_the_claim_is_selected():
    evidence, embedding = EvidenceService(), EmbeddingService()
    spans = evidence.spans(PASSAGE)
    claim = "The __init__() function is called when the class is initiated and assigns values."

    vectors = await embedding.embed_passages(spans + [claim])
    picked = evidence.select(spans, vectors[: len(spans)], vectors[len(spans) :])

    assert any("__init__() function is a special function" in span for span in picked)
    assert len(picked) <= 3
    assert not any("red convertible" in span for span in picked), "unrelated sentences stay out"
    assert len(" ".join(picked)) < len(PASSAGE) / 2, "far less than the whole passage is highlighted"


async def test_two_claims_select_two_different_sentences():
    evidence, embedding = EvidenceService(), EmbeddingService()
    spans = evidence.spans(PASSAGE)
    claims = [
        "Classes are a template for creating objects.",
        "The exercise asks you to create a red convertible called Fer worth $60,000.",
    ]
    vectors = await embedding.embed_passages(spans + claims)
    picked = evidence.select(spans, vectors[: len(spans)], vectors[len(spans) :])

    joined = " ".join(picked)
    assert "template to create your objects" in joined
    assert "red convertible" in joined


SQLITE_PASSAGE = (
    "SQLite was designed to allow the program to be operated without installing a database "
    "management system or requiring a database administrator. Due to the serverless design, "
    "SQLite applications require less configuration than client-server databases. SQLite stores "
    "the entire database as a single cross-platform file."
)
POSTGRES_PASSAGE = (
    "2026-09-24 - PostgreSQL 19 Beta 4 Released! The PostgreSQL Global Development Group announces "
    "that the 4th beta release of PostgreSQL 19 is now available for download. We strongly "
    "encourage you to test the new features of PostgreSQL 19 on your systems."
)


def test_supported_and_invented_claims_score_far_apart():
    evidence = EvidenceService()
    supported = evidence.support(
        "SQLite has a serverless design and stores the entire database in a single file.", [SQLITE_PASSAGE]
    )
    invented = evidence.support(
        "PostgreSQL is a client-server database management system that typically requires a server to run.",
        [POSTGRES_PASSAGE],
    )
    assert supported >= 0.8
    assert invented <= 0.4
    assert evidence.support("Yes, it does.", [POSTGRES_PASSAGE]) == 1.0, "too short to judge"


def test_citation_is_withdrawn_from_a_sentence_its_source_does_not_support():
    # the real failure: the model stated what it knew about PostgreSQL and
    # cited a page that is only a release announcement
    evidence = EvidenceService()
    answer = (
        "SQLite has a serverless design and stores the database in a single file [2]. "
        "PostgreSQL is a client-server system that typically requires a server to run [4]. "
        "The PostgreSQL page announces that the 4th beta release of PostgreSQL 19 is available [4]."
    )
    checked = evidence.mark_unsupported(answer, {2: SQLITE_PASSAGE, 4: POSTGRES_PASSAGE})

    assert "single file [2]." in checked, "a supported sentence keeps its citation"
    assert "requires a server to run [?]." in checked, "the invented one loses it"
    assert "is available [4]." in checked, "the same source still backs what it does say"


def test_a_sentence_reporting_what_a_page_lacks_keeps_its_citation():
    # "the page does not say X" cannot share words with the page, and it is
    # the honest sentence the model is asked to write
    evidence = EvidenceService()
    answer = (
        "The PostgreSQL page provides information about its latest beta release, "
        "but does not explicitly state its server requirements [4]. "
        "The second page does not say whether it needs a server [4]. "
        "There is no information about pricing on this page [4]."
    )
    assert evidence.mark_unsupported(answer, {4: POSTGRES_PASSAGE}) == answer


def test_tally_counts_verified_and_withdrawn_statements():
    evidence = EvidenceService()
    answer = (
        "SQLite has a serverless design and stores the database in a single file [2]. "
        "PostgreSQL is a client-server system that typically requires a server to run [4]. "
        "The PostgreSQL page announces that the 4th beta release of PostgreSQL 19 is available [4]. "
        "The PostgreSQL page does not say whether it needs a server [4]. "
        "This closing remark cites nothing."
    )
    checked = evidence.mark_unsupported(answer, {2: SQLITE_PASSAGE, 4: POSTGRES_PASSAGE})

    # two statements matched their page, one was withdrawn; the sentence about
    # what the page lacks and the uncited remark are counted in neither
    assert evidence.tally(checked) == {"verified": 2, "unverified": 1}
    assert evidence.tally("Nothing here is cited.") == {"verified": 0, "unverified": 0}


def test_checking_keeps_lists_and_line_breaks_intact():
    evidence = EvidenceService()
    answer = "Two points:\n\n- SQLite stores the entire database as a single file [1]\n- Bananas are an excellent source of potassium [1]"
    checked = evidence.mark_unsupported(answer, {1: SQLITE_PASSAGE})
    assert checked.split("\n")[:3] == answer.split("\n")[:3]
    assert checked.endswith("- Bananas are an excellent source of potassium [?]")


def test_nothing_is_selected_without_spans_or_claims():
    evidence = EvidenceService()
    assert evidence.select([], [], [[1.0, 0.0]]) == []
    assert evidence.select(["a sentence that is long enough"], [[1.0, 0.0]], []) == []
    # an unrelated claim (orthogonal vector) highlights nothing rather than something wrong
    assert evidence.select(["a sentence that is long enough"], [[1.0, 0.0]], [[0.0, 1.0]]) == []
