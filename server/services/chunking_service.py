"""Indexing stage — heading-aware chunking of Readability-extracted HTML.

Contract: this module is the sole producer of extracted_text, and
chunk.text == extracted_text[char_start:char_end] always. Anything that breaks
that (scrubbing after chunking, trimming text elsewhere) breaks citation
highlighting and re-chunking.

Pipeline: HTML -> flat blocks -> sections grouped by heading stack -> chunks
packed to a token budget (small sections merge, oversized ones split at block
boundaries with overlap, a single over-budget block splits at sentences).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken
from selectolax.parser import HTMLParser, Node

import config

_ENC = tiktoken.get_encoding("cl100k_base")

_BLOCK_TAGS = frozenset(
    {"p", "li", "blockquote", "pre", "td", "th", "dt", "dd", "figcaption"}
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "template"})

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ɏ0-9\"'(])")
_WS_RE = re.compile(r"\s+")


def _tok(text: str) -> int:
    return len(_ENC.encode(text, disallowed_special=()))


@dataclass(slots=True)
class Block:
    kind: str  # "heading" | "text" | "code"
    text: str
    level: int = 0
    start: int = -1  # offsets into canonical text, set by _assemble
    end: int = -1

    @property
    def tokens(self) -> int:
        return _tok(self.text)


@dataclass(slots=True)
class Chunk:
    ordinal: int
    heading_path: list[str]
    text: str
    token_count: int
    char_start: int
    char_end: int


@dataclass(slots=True)
class _Section:
    heading_path: list[str]
    blocks: list[Block] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(b.tokens for b in self.blocks)


def _node_text(node: Node, *, preserve_ws: bool) -> str:
    text = node.text(deep=True, separator=" ", strip=False)
    if preserve_ws:
        return text.strip("\n")
    return _WS_RE.sub(" ", text).strip()


def _walk(node: Node, blocks: list[Block]) -> None:
    for child in node.iter(include_text=False):
        tag = child.tag
        if tag in _SKIP_TAGS:
            continue
        if tag in _HEADING_TAGS:
            text = _node_text(child, preserve_ws=False)
            if text:
                blocks.append(Block("heading", text, level=int(tag[1])))
        elif tag == "pre":
            # collapsing internal newlines destroys code
            text = _node_text(child, preserve_ws=True).strip()
            if text:
                blocks.append(Block("code", text))
        elif tag in _BLOCK_TAGS:
            # prefer nested block structure (li > p) so text isn't captured twice
            if any(c.tag in _BLOCK_TAGS or c.tag in _HEADING_TAGS
                   for c in child.iter(include_text=False)):
                _walk(child, blocks)
            else:
                text = _node_text(child, preserve_ws=False)
                if text:
                    blocks.append(Block("text", text))
        else:
            _walk(child, blocks)


def _parse_blocks(html: str) -> list[Block]:
    tree = HTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return []
    blocks: list[Block] = []
    _walk(root, blocks)

    # no recognized block tags at all: treat the visible text as one paragraph
    if not blocks:
        text = _WS_RE.sub(" ", root.text(deep=True, separator=" ")).strip()
        if text:
            blocks = [Block("text", text)]
    return blocks


def _assemble(blocks: list[Block]) -> str:
    """Join blocks with a blank line and stamp each with its char offsets."""
    parts: list[str] = []
    pos = 0
    for b in blocks:
        if parts:
            pos += 2  # "\n\n" separator
        b.start = pos
        b.end = pos + len(b.text)
        pos = b.end
        parts.append(b.text)
    return "\n\n".join(parts)


def _group_sections(blocks: list[Block]) -> list[_Section]:
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    current = _Section(heading_path=[])

    for b in blocks:
        if b.kind == "heading":
            if current.blocks:
                sections.append(current)
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.text))
            current = _Section(heading_path=[t for _, t in stack])
            current.blocks.append(b)
        else:
            current.blocks.append(b)
    if current.blocks:
        sections.append(current)
    return sections


def _split_giant_block(b: Block) -> list[Block]:
    """Split one over-budget block at sentence boundaries, preserving offsets."""
    pieces: list[Block] = []
    offset = b.start
    for part in _SENTENCE_RE.split(b.text):
        if not part:
            continue
        # cursor advance resolves repeated sentences to their own occurrence
        rel = b.text.find(part, offset - b.start)
        start = b.start + rel
        pieces.append(Block(b.kind, part, start=start, end=start + len(part)))
        offset = start + len(part)
    return pieces or [b]


def _common_prefix(paths: list[list[str]]) -> list[str]:
    if not paths:
        return []
    prefix = paths[0]
    for p in paths[1:]:
        i = 0
        while i < len(prefix) and i < len(p) and prefix[i] == p[i]:
            i += 1
        prefix = prefix[:i]
    return list(prefix)


def _emit(
    chunks: list[Chunk],
    canonical: str,
    blocks: list[Block],
    heading_path: list[str],
) -> None:
    if not blocks:
        return
    start, end = blocks[0].start, blocks[-1].end
    text = canonical[start:end]
    chunks.append(
        Chunk(
            ordinal=len(chunks),
            heading_path=heading_path,
            text=text,
            token_count=_tok(text),
            char_start=start,
            char_end=end,
        )
    )


def chunk_html(
    html: str,
    *,
    target_tokens: int = config.CHUNK_TARGET_TOKENS,
    max_tokens: int = config.CHUNK_MAX_TOKENS,
    overlap_tokens: int = config.CHUNK_OVERLAP_TOKENS,
) -> tuple[str, list[Chunk]]:
    """Returns (extracted_text, chunks); store extracted_text verbatim."""
    blocks = _parse_blocks(html)
    if not blocks:
        return "", []

    canonical = _assemble(blocks)

    # explode blocks that alone exceed the cap so packing can treat blocks as atomic
    exploded: list[Block] = []
    for b in blocks:
        if b.kind != "heading" and b.tokens > max_tokens:
            exploded.extend(_split_giant_block(b))
        else:
            exploded.append(b)

    sections = _group_sections(exploded)
    chunks: list[Chunk] = []

    buf_blocks: list[Block] = []
    buf_paths: list[list[str]] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf_blocks, buf_paths, buf_tokens
        # merged sections carry their common heading prefix: a chunk must never
        # claim a deeper heading than covers all of its content
        _emit(chunks, canonical, buf_blocks, _common_prefix(buf_paths))
        buf_blocks, buf_paths, buf_tokens = [], [], 0

    for sec in sections:
        sec_tokens = sec.tokens

        if sec_tokens <= target_tokens:
            if buf_tokens and buf_tokens + sec_tokens > target_tokens:
                flush()
            buf_blocks.extend(sec.blocks)
            buf_paths.append(sec.heading_path)
            buf_tokens += sec_tokens
            continue

        if buf_tokens:
            flush()

        # oversized section: windows of blocks with trailing-token overlap
        window: list[Block] = []
        w_tokens = 0
        for b in sec.blocks:
            if window and w_tokens + b.tokens > target_tokens:
                _emit(chunks, canonical, window, sec.heading_path)
                carried: list[Block] = []
                c_tokens = 0
                for prev in reversed(window):
                    if c_tokens >= overlap_tokens or prev.kind == "heading":
                        break
                    carried.insert(0, prev)
                    c_tokens += prev.tokens
                window = carried
                w_tokens = c_tokens
            window.append(b)
            w_tokens += b.tokens
        _emit(chunks, canonical, window, sec.heading_path)

    if buf_tokens:
        flush()

    return canonical, chunks


class ChunkingService:
    """Splits extracted article HTML into heading-aware, overlapping chunks."""

    def chunk(self, html: str) -> tuple[str, list[Chunk]]:
        """Chunk one page using the token budgets from config.

        Args:
            html: Readability-extracted article HTML, already scrubbed.

        Returns:
            The canonical extracted text and its chunks; every chunk's text
            equals ``extracted_text[char_start:char_end]``.
        """

        return chunk_html(html)
