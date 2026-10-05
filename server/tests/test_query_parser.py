"""Parser tests — all offline. The LLM is simulated by feeding parse_response
raw strings; the invariant is that NO input can make it raise or emit an
implausible filter."""

from datetime import datetime, timezone

from langchain_core.runnables import RunnableLambda

from prompts.query_filter_prompt import QUERY_FILTER_PROMPT
from services.query_filter_service import ParsedQuery, QueryFilterService, parse_response

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)  # a Wednesday
Q = "what did I read about transformers last Tuesday?"


def test_prompt_contains_anchor_date_and_question():
    messages = QUERY_FILTER_PROMPT.format_messages(
        weekday="Wednesday", today="2026-07-29", timezone_offset="+0000",
        conversation="(none)", question=Q,
    )
    p = messages[-1].content
    assert "Wednesday" in p and "2026-07-29" in p
    assert p.rstrip().endswith(Q)
    # the JSON examples must survive templating as literal braces
    assert '{"semantic_query": "transformers"' in p


async def test_follow_up_question_is_sent_with_the_conversation():
    # "explain it more simply" can only be searched if the model is shown
    # what "it" refers to
    from schemas import HistoryTurn

    seen = {}

    def capture(prompt):
        seen["text"] = prompt.to_messages()[-1].content
        return '{"semantic_query": "accessing a variable inside an object"}'

    service = QueryFilterService(model=RunnableLambda(capture))
    history = [
        HistoryTurn(role="user", content="How do I access a variable inside an object?"),
        HistoryTurn(role="assistant", content="Use myobjectx.variable {braces are harmless} [1]."),
    ]
    q = await service.parse("can you explain it in a more understandable way", history, now=NOW)

    assert "User: How do I access a variable inside an object?" in seen["text"]
    assert "Assistant: Use myobjectx.variable {braces are harmless}" in seen["text"]
    assert q.semantic_query == "accessing a variable inside an object"

    await service.parse("a first question", now=NOW)
    assert "(none)" in seen["text"]


def test_valid_response_parsed():
    raw = '{"semantic_query": "transformers", "since": "2026-07-28T00:00:00", "until": "2026-07-29T00:00:00", "domains": null}'
    q = parse_response(raw, Q, NOW)
    assert q.semantic_query == "transformers"
    assert q.since is not None and q.since.day == 28
    assert q.until is not None and q.until.day == 29
    assert q.domains is None


def test_markdown_fenced_json_accepted():
    raw = '```json\n{"semantic_query": "pricing", "since": null, "until": null, "domains": ["stripe.com"]}\n```'
    q = parse_response(raw, Q, NOW)
    assert q.semantic_query == "pricing"
    assert q.domains == ["stripe.com"]


def test_garbage_falls_back_to_unfiltered():
    for raw in ["not json at all", "[1,2,3]", "", '"just a string"']:
        q = parse_response(raw, Q, NOW)
        assert q == ParsedQuery(semantic_query=Q), raw


def test_empty_semantic_query_falls_back_to_question():
    q = parse_response('{"semantic_query": "", "since": null}', Q, NOW)
    assert q.semantic_query == Q


def test_future_since_dropped():
    q = parse_response('{"semantic_query": "x", "since": "2027-01-01T00:00:00"}', Q, NOW)
    assert q.since is None


def test_inverted_window_drops_until():
    raw = '{"semantic_query": "x", "since": "2026-07-28T00:00:00", "until": "2026-07-20T00:00:00"}'
    q = parse_response(raw, Q, NOW)
    assert q.since is not None
    assert q.until is None


def test_ancient_since_dropped():
    q = parse_response('{"semantic_query": "x", "since": "1970-01-01T00:00:00"}', Q, NOW)
    assert q.since is None


def test_invalid_date_string_dropped():
    q = parse_response('{"semantic_query": "x", "since": "last Tuesday"}', Q, NOW)
    assert q.since is None


def test_domains_normalized():
    raw = '{"semantic_query": "x", "domains": ["WWW.GitHub.com", "  ", 42, "stripe.com"]}'
    q = parse_response(raw, Q, NOW)
    assert q.domains == ["github.com", "stripe.com"]


def test_naive_datetime_gets_timezone():
    q = parse_response('{"semantic_query": "x", "since": "2026-07-28T00:00:00"}', Q, NOW)
    assert q.since is not None and q.since.tzinfo is not None


async def test_llm_failure_degrades_gracefully():
    def provider_down(_prompt):
        raise RuntimeError("provider down")

    service = QueryFilterService(model=RunnableLambda(provider_down))
    q = await service.parse(Q, now=NOW)
    assert q == ParsedQuery(semantic_query=Q)


async def test_service_parses_model_output_end_to_end():
    raw = '{"semantic_query": "transformers", "since": "2026-07-28T00:00:00", "until": null, "domains": null}'
    service = QueryFilterService(model=RunnableLambda(lambda _prompt: raw))
    q = await service.parse(Q, now=NOW)
    assert q.semantic_query == "transformers"
    assert q.since is not None and q.since.day == 28
