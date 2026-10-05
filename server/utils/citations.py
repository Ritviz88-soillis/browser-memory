"""Citation validation, applied after generation. Pure functions, no I/O."""

import re
from typing import List, Tuple

from utils.formatting import Source

# "[2]" and also grouped forms some models write: "[2, 4]" or "[2,4,7]"
_CITATION_RE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def validate_citations(
    answer: str,
    sources: List[Source],
) -> Tuple[str, List[Source]]:
    """Strip citations of sources that were never provided.

    A hallucinated number, or one injected by page text, is removed so the
    answer can only point at sources the model was actually given. Grouped
    citations are rewritten one per bracket ("[2, 4]" becomes "[2][4]") so
    every citation in the answer has the same, individually clickable form.

    Args:
        answer: The raw model answer.
        sources: Every source the model was shown.

    Returns:
        The cleaned answer and the sources it actually cites.
    """

    valid_numbers = {source.n for source in sources}
    cited_numbers: set[int] = set()

    def keep_valid(match: re.Match[str]) -> str:
        numbers = [int(number) for number in match.group(1).split(",")]
        kept = [number for number in numbers if number in valid_numbers]
        cited_numbers.update(kept)
        return "".join(f"[{number}]" for number in kept)

    cleaned = _CITATION_RE.sub(keep_valid, answer)
    return cleaned, [source for source in sources if source.n in cited_numbers]
