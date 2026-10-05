"""Prompt template for self-query filter extraction.

Turns "that article I read last Tuesday about HNSW" into a topical query plus
date and site filters, so temporal references run as SQL filters instead of
confusing vector search.
"""

from langchain_core.prompts import ChatPromptTemplate

_QUERY_FILTER_INSTRUCTIONS = """You extract search filters from questions about someone's browsing history.
Today is {weekday}, {today} (timezone offset {timezone_offset}).

Return ONLY a JSON object, no prose, with these keys:
- "semantic_query": the topical part of the question, stripped of time/site references.
  It must make sense on its own. If the question refers back to the conversation
  ("explain it more simply", "what about its licence?"), replace the reference with
  the topic being discussed. Never empty; if the question is purely temporal
  ("what did I read yesterday?"), use a short paraphrase like "pages read".
- "since": ISO 8601 datetime, inclusive lower bound, or null. Resolve relative
  references against today's date: "yesterday" = start of yesterday, "last week" =
  start of Monday last week, "last Tuesday" = most recent Tuesday before today.
- "until": ISO 8601 datetime, exclusive upper bound, or null. "yesterday" also sets
  until = start of today. Open-ended references ("since Monday", "recently") leave
  until null.
- "domains": list of bare domains mentioned ("on github" -> ["github.com"]), or null.

Examples (assuming today is Wednesday 2026-07-29):
Q: what did I read about transformers last Tuesday?
{{"semantic_query": "transformers", "since": "2026-07-28T00:00:00", "until": "2026-07-29T00:00:00", "domains": null}}
Q: that pricing page I saw on stripe's site last month
{{"semantic_query": "pricing page", "since": "2026-06-01T00:00:00", "until": "2026-07-01T00:00:00", "domains": ["stripe.com"]}}
Q: how does HNSW indexing work?
{{"semantic_query": "HNSW indexing", "since": null, "until": null, "domains": null}}
Q: can you explain it in a simpler way
(after a conversation about accessing a variable inside an object)
{{"semantic_query": "accessing a variable inside an object", "since": null, "until": null, "domains": null}}

Conversation so far, oldest first (use it only to resolve words like "it" or "that"):
{conversation}

Q: {question}"""

QUERY_FILTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "You are a precise JSON extraction function."),
        ("human", _QUERY_FILTER_INSTRUCTIONS),
    ]
)
