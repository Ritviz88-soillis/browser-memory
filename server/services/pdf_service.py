"""PDF reading: turn a PDF file into article HTML the chunker understands.

The browser shows PDFs in a built-in viewer that extensions cannot read, so
the extension sends the file itself and the text is extracted here. Each page
becomes a "Page N" section, so every passage knows which page it came from and
a citation can open the PDF at that page.

Scanned PDFs are images with no text layer; they cannot be read this way.
"""

import html as html_lib
import io
from collections import Counter
from typing import List, Optional

from pypdf import PdfReader

import config


class PdfService:
    """Extracts the text of a PDF, page by page."""

    def to_html(self, data: bytes, title: Optional[str] = None) -> str:
        """Convert a PDF into HTML with one section per page.

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

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [
                [line.strip() for line in (page.extract_text() or "").splitlines()]
                for page in reader.pages[: config.PDF_MAX_PAGES]
            ]
            embedded_title = (reader.metadata.title if reader.metadata else None) or None
        except Exception as error:
            raise ValueError(f"this file could not be read as a PDF ({type(error).__name__})") from error

        furniture = self._page_furniture(pages)

        sections: List[str] = []
        for number, lines in enumerate(pages, start=1):
            paragraphs = self._paragraphs(lines, furniture)
            if paragraphs:
                body = "".join(f"<p>{html_lib.escape(paragraph)}</p>" for paragraph in paragraphs)
                sections.append(f"<h2>Page {number}</h2>{body}")

        if not sections:
            raise ValueError("this PDF has no readable text (it may be a scanned document)")

        heading = html_lib.escape((embedded_title or title or "PDF document").strip())
        return f"<article><h1>{heading}</h1>{''.join(sections)}</article>"

    def _page_furniture(self, pages: List[List[str]]) -> set:
        """Find running headers and footers: lines repeated on most pages.

        Args:
            pages: The lines of each page.

        Returns:
            The repeated lines, which carry no content of their own.
        """

        if len(pages) < 3:
            return set()
        counts = Counter(line for lines in pages for line in set(lines) if line)
        return {line for line, count in counts.items() if count > len(pages) / 2}

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
