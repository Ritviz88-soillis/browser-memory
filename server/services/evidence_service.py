"""Evidence: which sentences of a cited passage actually support the answer.

A passage can be several paragraphs, and a short page is a single passage.
Highlighting the whole thing tells the reader nothing, so after an answer is
written this service narrows each cited passage down to the few sentences
closest in meaning to the claims that cite it.

It only splits text and compares vectors; the orchestrator embeds the pieces
in between.
"""

import re
from typing import Dict, List, Sequence

import numpy as np

import config

_BLOCK_SPLIT_RE = re.compile(r"\n\s*\n")
# A sentence ends at . ! ? followed by whitespace, or at the end of the block.
# A dot inside a word ("myobject.variable", "3.14") does not end it.
_SENTENCE_RE = re.compile(r".+?(?:[.!?]+(?=\s)|$)")
_CITATION_RE = re.compile(r"\[(\d{1,3})\]")
# An answer sentence keeps the citations written just after its full stop
# ("...blocks writers. [2]"), so they are credited to the right claim.
_ANSWER_SENTENCE_RE = re.compile(r".+?(?:[.!?:]+(?:\s*\[\d{1,3}\])*(?=\s|$)|$)")
_MARKUP_RE = re.compile(r"[`*_#>]|^\s*[-•]\s+")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([.,;:!?])")
_CITATIONS_IN_SENTENCE_RE = re.compile(r"(?:\s*\[\d{1,3}\])+")
_WORD_RE = re.compile(r"[a-z0-9_]+")

# Replaces the citations of a sentence the cited page does not support.
UNVERIFIED_MARK = "[?]"

# Words that carry no claim of their own, so they are not counted when
# checking whether a sentence is supported by a page.
_FILLER_WORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how i if in
    into is it its like may me more most my no not of on one or other so some such than that the
    their them then there these they this those to too two up use used uses using was we were what
    when where whether which while who why will with would you your also each any all both only
    very just about over between through during being here own same called means meaning allows
    allowing typically usually often based overall contrast hand whereas however therefore thus
    example instance looking different page pages tab tabs source sources website site says say
    said states state stated mentions mention mentioned discusses discuss provides provide
    information according explicitly""".split()
)

# A sentence saying what a source does NOT contain cannot share words with it,
# and is exactly what the model is asked to write; it is never withdrawn.
_REPORTS_ABSENCE_RE = re.compile(
    r"\b(?:does not|doesn't|do not|don't|did not|didn't|not)\s+(?:\w+\s+){0,2}?"
    r"(?:state|say|mention|cover|compare|discuss|contain|include|provide|describe|address|"
    r"explain|specify|give|list)\b"
    r"|\bno (?:information|mention|details?)\b|\bnothing (?:about|on)\b",
    re.IGNORECASE,
)


def _stem(word: str) -> str:
    """Crude stemming: "requires", "required", "requiring" compare equal."""

    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def content_words(text: str) -> set:
    """The meaningful words of a text, lowercased and stemmed."""

    return {
        _stem(word)
        for word in _WORD_RE.findall(text.lower())
        if len(word) > 2 and word not in _FILLER_WORDS
    }


class EvidenceService:
    """Picks the sentences of a passage that back up an answer, and checks
    that each cited sentence of the answer is supported by what it cites."""

    def support(self, claim: str, passages: Sequence[str]) -> float:
        """How much of a claim's content appears in the passages it cites.

        Args:
            claim: One sentence of the answer.
            passages: The text of the sources that sentence cites.

        Returns:
            The share (0 to 1) of the claim's meaningful words found in the
            passages. A claim with too few such words to judge scores 1.
        """

        claim_words = content_words(claim)
        if len(claim_words) < config.SUPPORT_MIN_WORDS:
            return 1.0
        passage_words = content_words(" ".join(passages))
        return len(claim_words & passage_words) / len(claim_words)

    def mark_unsupported(self, answer: str, passages: Dict[int, str]) -> str:
        """Withdraw the citation from sentences their source does not support.

        A language model can state something it already knows and attach a
        citation to it. Such a sentence shares few words with the page it
        points at; its citation is replaced with ``UNVERIFIED_MARK`` so the
        reader is told it was not found there.

        This catches claims about things the page never mentions. It cannot
        catch a false sentence assembled from words that are on the page.

        Args:
            answer: The validated answer, with [n] citations.
            passages: Source number -> the passage text of that source.

        Returns:
            The answer with unsupported citations replaced by the mark.
        """

        def check(match: re.Match) -> str:
            sentence = match.group(0)
            numbers = [int(number) for number in _CITATION_RE.findall(sentence)]
            cited = [passages[number] for number in numbers if number in passages]
            if not cited:
                return sentence
            claim = _MARKUP_RE.sub("", _CITATION_RE.sub("", sentence))
            if _REPORTS_ABSENCE_RE.search(claim):
                return sentence
            if self.support(claim, cited) >= config.SUPPORT_MIN_WORD_OVERLAP:
                return sentence
            return _CITATIONS_IN_SENTENCE_RE.sub(" " + UNVERIFIED_MARK, sentence)

        return "\n".join(
            _ANSWER_SENTENCE_RE.sub(check, line) if line.strip() else line
            for line in answer.split("\n")
        )

    def tally(self, checked_answer: str) -> Dict[str, int]:
        """Count how an answer's cited statements fared in the check.

        Args:
            checked_answer: The answer after ``mark_unsupported``.

        Returns:
            ``verified``: statements that kept their citation (they matched
            the page they cite). ``unverified``: statements whose citation
            was withdrawn. Statements reporting what a page lacks, and
            sentences that cite nothing, are counted in neither.
        """

        verified = 0
        for line in checked_answer.splitlines():
            for match in _ANSWER_SENTENCE_RE.finditer(line.strip()):
                sentence = match.group(0)
                if _CITATION_RE.search(sentence) and not _REPORTS_ABSENCE_RE.search(sentence):
                    verified += 1
        return {"verified": verified, "unverified": checked_answer.count(UNVERIFIED_MARK)}

    def spans(self, passage: str) -> List[str]:
        """Split a passage into candidate spans to highlight.

        Prose is split into sentences. A block that looks like code (it keeps
        its line breaks) stays whole. Every span is an exact substring of the
        passage, so it can be located on the page.

        Args:
            passage: The cited passage text.

        Returns:
            The spans, in passage order.
        """

        spans: List[str] = []
        for block in _BLOCK_SPLIT_RE.split(passage):
            block = block.strip()
            if len(block) < config.EVIDENCE_MIN_SPAN_CHARS:
                continue
            if "\n" in block:
                spans.append(block)
                continue
            for match in _SENTENCE_RE.finditer(block):
                sentence = match.group(0).strip()
                if len(sentence) >= config.EVIDENCE_MIN_SPAN_CHARS:
                    spans.append(sentence)
        return spans

    def claims(self, answer: str) -> Dict[int, List[str]]:
        """Group the answer's sentences by the sources they cite.

        Args:
            answer: The validated answer text, with [n] citations.

        Returns:
            Source number -> the answer sentences that cite it, with the
            citation markers and formatting characters removed.
        """

        claims: Dict[int, List[str]] = {}
        for line in answer.splitlines():
            for match in _ANSWER_SENTENCE_RE.finditer(line.strip()):
                piece = match.group(0)
                numbers = {int(number) for number in _CITATION_RE.findall(piece)}
                text = _MARKUP_RE.sub("", _CITATION_RE.sub("", piece))
                text = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", text).strip()
                if not numbers or len(text) < config.EVIDENCE_MIN_SPAN_CHARS:
                    continue
                # "the page does not say X" has no sentence on the page to point at
                if _REPORTS_ABSENCE_RE.search(text):
                    continue
                for number in numbers:
                    claims.setdefault(number, []).append(text)
        return claims

    def select(
        self,
        spans: Sequence[str],
        span_vectors: Sequence[Sequence[float]],
        claim_vectors: Sequence[Sequence[float]],
    ) -> List[str]:
        """Choose the spans that best support the claims.

        Each claim votes for the span closest to it in meaning. Spans that are
        close enough are kept; if none is, the single best one is kept when it
        is at least loosely related, so a citation still points somewhere.

        Args:
            spans: Candidate spans of one passage, in passage order.
            span_vectors: One embedding per span.
            claim_vectors: One embedding per claim citing this passage.

        Returns:
            Up to ``config.EVIDENCE_MAX_SPANS`` spans, in passage order.
        """

        if not spans or not len(claim_vectors):
            return []

        span_matrix = _unit(np.asarray(span_vectors, dtype=np.float32))
        claim_matrix = _unit(np.asarray(claim_vectors, dtype=np.float32))
        similarity = claim_matrix @ span_matrix.T  # claims x spans

        # each claim's single closest span, and how close it is
        votes: Dict[int, float] = {}
        for claim_index in range(similarity.shape[0]):
            span_index = int(np.argmax(similarity[claim_index]))
            score = float(similarity[claim_index, span_index])
            votes[span_index] = max(votes.get(span_index, 0.0), score)

        by_score = sorted(votes, key=lambda index: -votes[index])
        strong = [
            index for index in by_score if votes[index] >= config.EVIDENCE_MIN_SIMILARITY
        ][: config.EVIDENCE_MAX_SPANS]

        if not strong:
            best = by_score[0]
            if votes[best] < config.EVIDENCE_FALLBACK_SIMILARITY:
                return []
            strong = [best]

        return [spans[index] for index in sorted(strong)]


def _unit(matrix: np.ndarray) -> np.ndarray:
    """Scale each row to length 1, so a dot product is cosine similarity."""

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)
