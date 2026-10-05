"""PDF text extraction. The PDFs are built in memory, so no file is needed."""

import pytest

from services.chunking_service import chunk_html
from services.pdf_service import PdfService


def make_pdf(pages: list[list[str]]) -> bytes:
    """Build a minimal, valid PDF with the given lines of text on each page."""

    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, lines in enumerate(pages):
        text = " 0 -16 Td ".join(f"({line}) Tj" for line in lines)
        stream = f"BT /F1 12 Tf 72 720 Td {text} ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return out


PAPER = make_pdf(
    [
        ["Journal of Testing, Volume 1", "Mobile devices help advanced learners study English.", "1"],
        ["Journal of Testing, Volume 1", "Participants used smartphones mostly for vocabulary.", "2"],
        ["Journal of Testing, Volume 1", "The study concludes that autonomy matters.", "3"],
    ]
)


def test_each_page_becomes_a_section_and_running_headers_are_removed():
    html = PdfService().to_html(PAPER, title="EJ1172284.pdf")

    assert html.startswith("<article><h1>EJ1172284.pdf</h1>")
    assert "<h2>Page 1</h2><p>Mobile devices help advanced learners study English.</p>" in html
    assert "<h2>Page 3</h2><p>The study concludes that autonomy matters.</p>" in html
    assert "Journal of Testing" not in html, "a line repeated on every page is a running header"
    assert "<p>2</p>" not in html, "bare page numbers are dropped"


def test_passages_know_which_page_they_came_from():
    _, chunks = chunk_html(PdfService().to_html(PAPER, title="A paper"))
    vocabulary = next(c for c in chunks if "vocabulary" in c.text)
    assert "Page 2" in vocabulary.text or vocabulary.heading_path[-1] == "Page 2"


def test_paragraphs_are_rebuilt_from_wrapped_lines():
    lines = ["The concept of autonomy in", "language learning is old.", "", "A second paragraph", "follows here.", ""]
    assert PdfService()._paragraphs(lines, furniture=set()) == [
        "The concept of autonomy in language learning is old.",
        "A second paragraph follows here.",
    ]


def test_text_that_looks_like_markup_is_escaped():
    html = PdfService().to_html(make_pdf([["Use the <script> tag carefully in HTML."]]))
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_unreadable_files_are_refused_with_a_reason():
    with pytest.raises(ValueError, match="could not be read as a PDF"):
        PdfService().to_html(b"this is not a pdf at all")
    with pytest.raises(ValueError, match="no readable text"):
        PdfService().to_html(make_pdf([[]]))
