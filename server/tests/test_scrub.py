"""Scrubber tests. The false-positive cases matter as much as the catches —
a scrubber that eats ordinary prose corrupts the corpus silently."""

from utils.scrub import scrub


def test_vendor_keys_redacted():
    text = (
        "config: OPENAI_API_KEY=sk-abc123def456ghi789jkl012 and "
        "aws AKIAIOSFODNN7EXAMPLE plus ghp_" + "a1B2" * 9 + " done"
    )
    r = scrub(text)
    assert "[REDACTED:openai-key]" in r.text
    assert "[REDACTED:aws-key]" in r.text
    assert "[REDACTED:github-token]" in r.text
    assert "sk-abc123" not in r.text
    assert r.total == 3


def test_private_key_block_redacted():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n-----END RSA PRIVATE KEY-----"
    r = scrub(f"here is my key {pem} please help")
    assert r.findings == {"private-key": 1}
    assert "MIIEow" not in r.text


def test_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    r = scrub(f"Authorization: Bearer {jwt}")
    assert r.findings == {"jwt": 1}


def test_luhn_valid_card_redacted():
    r = scrub("I paid with 4532 0151 1283 0366 yesterday")  # Luhn-valid
    assert r.findings == {"card": 1}
    assert "4532" not in r.text


def test_luhn_invalid_number_survives():
    # 16 digits but fails Luhn: an order id, not a card. Must NOT be eaten.
    r = scrub("order number 1234 5678 9012 3456 has shipped")
    assert r.total == 0
    assert "1234 5678 9012 3456" in r.text


def test_ordinary_prose_untouched():
    text = (
        "The transaction used a token-based flow. Skills like sklearn matter. "
        "Call 555-0142 or read chapter 12 of the 2026 report."
    )
    r = scrub(text)
    assert r.text == text
    assert r.total == 0


def test_counts_multiple_of_same_kind():
    r = scrub("first sk-aaaaaaaaaaaaaaaaaaaaaaaa then sk-bbbbbbbbbbbbbbbbbbbbbbbb")
    assert r.findings == {"openai-key": 2}
