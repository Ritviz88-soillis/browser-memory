"""Citation validation, applied after generation. Pure functions, no I/O."""

import re
from typing import List, Tuple

from utils.formatting import Source

_CITATION_RE = re.compile(r"\[(\d{1,3})\]")


def validate_citations(
    answer: str,
    sources: List[Source],
) -> Tuple[str, List[Source]]:
    """Strip citations of sources that were never provided.

    A hallucinated number, or one injected by page text, is removed so the
    answer can only point at sources the model was actually given.

    Args:
        answer: The raw model answer.
        sources: Every source the model was shown (including source [0]).

    Returns:
        The cleaned answer and the sources it actually cites.
    """

    valid_numbers = {source.n for source in sources}
    cited_numbers: set[int] = set()

    def keep_or_strip(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number in valid_numbers:
            cited_numbers.add(number)
            return match.group(0)
        return ""

    cleaned = _CITATION_RE.sub(keep_or_strip, answer)
    return cleaned, [source for source in sources if source.n in cited_numbers]
