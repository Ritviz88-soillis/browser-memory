"""PDF reading: turn a PDF file into article HTML the chunker understands.

The browser shows PDFs in a built-in viewer that extensions cannot read, so
the extension sends the file itself and the text is extracted here. Each page
becomes a "Page N" section, so every passage knows which page it came from and
a citation can open the PDF at that page.

A long document is read a range of pages at a time (``read_pages`` +
``pages_html``), so it can be indexed in batches. Scanned PDFs are images with
no text layer; they cannot be read this way.
"""

import html as html_lib
import io
from collections import Counter
from pathlib import Path
from typing import List, Optional, Sequence, Union

from pypdf import PdfReader

import config

PdfSource = Union[bytes, str, Path]  # the file's bytes, or where it is saved


class PdfService:
    """Extracts the text of a PDF, page by page."""

    def looks_like_pdf(self, data: bytes) -> bool:
        """Cheap check that some bytes are a PDF at all (its signature).

        Args:
            data: The file's bytes.

        Returns:
            True if the PDF signature appears where the format requires it.
        """

        return b"%PDF-" in data[:1024]

    def page_count(self, source: PdfSource) -> int:
        """Count the pages that will be read.

        Args:
            source: The PDF's bytes or path.

        Returns:
            The number of pages, capped at ``config.PDF_MAX_PAGES``.

        Raises:
            ValueError: If the file is not a readable PDF.
        """

        return min(len(self._open(source).pages), config.PDF_MAX_PAGES)

    def title(self, source: PdfSource) -> Optional[str]:
        """Return the title stored inside the PDF, if it has one."""

        metadata = self._open(source).metadata
        return (metadata.title if metadata else None) or None

    def read_pages(self, source: PdfSource, start: int, stop: int) -> List[List[str]]:
        """Extract the text lines of a range of pages.

        Args:
            source: The PDF's bytes or path.
            start: Index of the first page to read (0-based).
            stop: Index after the last page to read.

        Returns:
            For each page in the range, its lines of text, stripped.

        Raises:
            ValueError: If the file is not a readable PDF.
        """

        reader = self._open(source)
        try:
            return [
                [line.strip() for line in (page.extract_text() or "").splitlines()]
                for page in reader.pages[start:stop]
            ]
        except Exception as error:
            raise ValueError(f"this file could not be read as a PDF ({type(error).__name__})") from error

    def furniture(self, pages: Sequence[List[str]]) -> List[str]:
        """Find running headers and footers: lines repeated on most pages.

        Args:
            pages: The lines of several pages (a few pages are enough).

        Returns:
            The repeated lines, which carry no content of their own.
        """

        if len(pages) < 3:
            return []
        counts = Counter(line for lines in pages for line in set(lines) if line)
        return sorted(line for line, count in counts.items() if count > len(pages) / 2)

    def pages_html(
        self,
        pages: Sequence[List[str]],
        first_page_number: int,
        furniture: Sequence[str] = (),
        title: Optional[str] = None,
    ) -> str:
        """Render a range of pages as HTML, one "Page N" section each.

        Args:
            pages: The lines of each page, from ``read_pages``.
            first_page_number: The page number (1-based) of the first one.
            furniture: Running header/footer lines to leave out.
            title: The document title, placed as the top heading.

        Returns:
            Article HTML, or an empty string if these pages have no text.
        """

        ignored = set(furniture)
        sections: List[str] = []
        for number, lines in enumerate(pages, start=first_page_number):
            paragraphs = self._paragraphs(lines, ignored)
            if paragraphs:
                body = "".join(f"<p>{html_lib.escape(paragraph)}</p>" for paragraph in paragraphs)
                sections.append(f"<h2>Page {number}</h2>{body}")

        if not sections:
            return ""
        heading = html_lib.escape((title or "PDF document").strip())
        return f"<article><h1>{heading}</h1>{''.join(sections)}</article>"

    def to_html(self, data: bytes, title: Optional[str] = None) -> str:
        """Convert a whole (short) PDF into HTML with one section per page.

        Args:
            data: The PDF file's bytes.
            title: A title to use when the PDF does not carry one.

        Returns:
            HTML with the title as <h1> and each page as <h2>Page N</h2>
            followed by its paragraphs.

        Raises:
            ValueError: If the file is not a readable PDF, or has no text
                (a scanned document).
        """

        pages = self.read_pages(data, 0, self.page_count(data))
        article_html = self.pages_html(
            pages, 1, self.furniture(pages), self.title(data) or title
        )
        if not article_html:
            raise ValueError("this PDF has no readable text (it may be a scanned document)")
        return article_html

    def _open(self, source: PdfSource) -> PdfReader:
        """Open a PDF from its bytes or its path."""

        try:
            return PdfReader(io.BytesIO(source) if isinstance(source, bytes) else str(source))
        except Exception as error:
            raise ValueError(f"this file could not be read as a PDF ({type(error).__name__})") from error

    def _paragraphs(self, lines: List[str], furniture: set) -> List[str]:
        """Rebuild paragraphs from a page's lines.

        A PDF stores text line by line; consecutive lines are joined, and a
        blank line starts a new paragraph. Running headers and bare page
        numbers are dropped.

        Args:
            lines: The page's lines, stripped.
            furniture: Lines to ignore (running headers and footers).

        Returns:
            The page's paragraphs.
        """

        paragraphs: List[str] = []
        current: List[str] = []
        for line in lines + [""]:
            if line and line not in furniture and not line.isdigit():
                current.append(line)
            elif not line and current:
                paragraphs.append(" ".join(current))
                current = []
        return paragraphs
