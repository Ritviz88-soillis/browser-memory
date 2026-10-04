"""Chunker tests.

The invariants here are load-bearing for the whole system:
  * offsets: chunk.text == extracted_text[char_start:char_end], always —
    citation highlighting and re-chunking both depend on it;
  * ordinals are dense and ordered;
  * token budgets are respected;
  * heading breadcrumbs reflect the h1..h6 nesting.
"""

import pytest

from services.chunking_service import chunk_html


def para(n_sentences: int, tag: str = "p") -> str:
    body = " ".join(
        f"This is sentence number {i} padding the paragraph with words." for i in range(n_sentences)
    )
    return f"<{tag}>{body}</{tag}>"


DOC = f"""
<html><body>
  <h1>Guide</h1>
  <p>Intro paragraph about the guide.</p>
  <h2>Install</h2>
  <h3>Windows</h3>
  {para(3)}
  <h3>Linux</h3>
  {para(3)}
  <h2>Usage</h2>
  {para(40)}
  {para(40)}
  <pre>def hello():\n    return "world"</pre>
</body></html>
"""


def _invariants(text: str, chunks):
    assert chunks, "expected at least one chunk"
    for i, c in enumerate(chunks):
        assert c.ordinal == i, "ordinals must be dense and ordered"
        assert c.text == text[c.char_start : c.char_end], "offset contract broken"
        assert c.char_end > c.char_start
        assert c.token_count > 0


def test_offset_contract_holds():
    text, chunks = chunk_html(DOC)
    _invariants(text, chunks)


def test_heading_paths_reflect_nesting():
    # Budget small enough that Windows/Linux stay separate sections: their
    # full h1>h2>h3 breadcrumb must survive.
    text, chunks = chunk_html(DOC, target_tokens=30, max_tokens=200, overlap_tokens=10)
    paths = [tuple(c.heading_path) for c in chunks]
    assert ("Guide", "Install", "Windows") in paths, paths
    assert any(p and p[0] == "Guide" for p in paths)


def test_merged_sections_carry_common_prefix():
    # At the default budget the tiny sections merge, and the merged chunk's
    # breadcrumb must collapse to the sections' common prefix — never claim a
    # deeper heading than covers ALL of its content.
    text, chunks = chunk_html(DOC)
    merged = chunks[0]
    assert "Intro paragraph" in merged.text and "Windows" in merged.text
    assert merged.heading_path == ["Guide"], merged.heading_path


def test_token_budget_respected():
    text, chunks = chunk_html(DOC, target_tokens=120, max_tokens=200, overlap_tokens=20)
    _invariants(text, chunks)
    for c in chunks:
        assert c.token_count <= 200 + 40, f"chunk {c.ordinal} is {c.token_count} tokens"


def test_long_section_splits_with_overlap():
    text, chunks = chunk_html(DOC, target_tokens=120, max_tokens=200, overlap_tokens=30)
    usage = [c for c in chunks if c.heading_path[-1:] == ["Usage"]]
    assert len(usage) >= 2, "the Usage section should split into several chunks"
    # consecutive chunks in a split section share overlapping char ranges
    overlapping = any(
        b.char_start < a.char_end for a, b in zip(usage, usage[1:])
    )
    assert overlapping, "expected token overlap between consecutive chunks"


def test_small_sections_merge():
    html = "<h2>A</h2><p>one line</p><h2>B</h2><p>another line</p>"
    text, chunks = chunk_html(html)
    _invariants(text, chunks)
    assert len(chunks) == 1, "two tiny sections should merge into one chunk"
    assert chunks[0].heading_path == [], "merged A+B share no common heading prefix"


def test_code_block_survives_intact():
    text, chunks = chunk_html(DOC)
    joined = "\n".join(c.text for c in chunks)
    assert 'def hello():' in joined
    assert 'return "world"' in joined
    assert "def hello():\n" in text, "code newlines must survive extraction"


def test_no_headings_at_all():
    text, chunks = chunk_html(f"<div>{para(4)}{para(4)}</div>")
    _invariants(text, chunks)
    assert all(c.heading_path == [] for c in chunks)


def test_empty_and_junk_input():
    assert chunk_html("") == ("", [])
    assert chunk_html("<script>alert(1)</script><style>p{}</style>") == ("", [])


def test_giant_single_paragraph_splits():
    html = f"<p>{' '.join(f'Sentence number {i} keeps flowing onward.' for i in range(400))}</p>"
    text, chunks = chunk_html(html, target_tokens=150, max_tokens=200)
    _invariants(text, chunks)
    assert len(chunks) >= 2, "a paragraph over max_tokens must be split"
    for c in chunks:
        assert c.token_count <= 260


def test_nested_list_paragraphs_not_duplicated():
    html = "<ul><li><p>alpha beta</p></li><li><p>gamma delta</p></li></ul>"
    text, chunks = chunk_html(html)
    _invariants(text, chunks)
    assert text.count("alpha beta") == 1, "nested li>p must not duplicate text"


@pytest.mark.parametrize("target", [80, 200, 450])
def test_invariants_across_budgets(target):
    text, chunks = chunk_html(DOC, target_tokens=target, max_tokens=target + 150)
    _invariants(text, chunks)
