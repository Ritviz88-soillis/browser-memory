# browser-memory

Semantic browser memory: a Chrome (MV3) extension that indexes pages you
actually read, with a RAG pipeline answering questions grounded in that
corpus — with citations.

## Repo layout (convention — keep new code inside these boundaries)

```
browser-memory/
├── extension/            # WXT + React + TS (own package.json)
│   ├── entrypoints/
│   │   ├── background.ts # tab tracking, 10 s reading threshold, upload queue
│   │   ├── content.ts    # Readability extraction on a copy of the page
│   │   └── sidepanel/    # React UI: Ask (chat, current page, related pages),
│   │                     #   Pages, Settings
│   └── utils/            # api client, privacy gates, queue, chat history
├── server/               # Python project root (own pyproject.toml, uv)
│   ├── main.py           # FastAPI app + lifespan (database, ingest worker)
│   ├── config.py         # every tunable: models, chunk sizes, top-k, caps
│   ├── router.py         # HTTP endpoints — thin, hand off to the orchestrator
│   ├── orchestrator.py   # EVERY flow as numbered steps; the only caller of services
│   ├── schemas/          # Pydantic request/response models (the wire contract)
│   ├── prompts/          # answer_prompt.py, query_filter_prompt.py
│   ├── services/         # one job each; no service imports another
│   │   ├── chunking_service.py      # heading-aware chunks with overlap
│   │   ├── embedding_service.py     # local bge-small / Jina embeddings
│   │   ├── ingestion_service.py     # the indexing queue; storing a processed page
│   │   ├── query_filter_service.py  # date + site filters from the question
│   │   ├── retrieval_service.py     # hybrid (vector + keyword) search of memory
│   │   ├── live_page_service.py     # which passages of an open tab to cite
│   │   ├── generation_service.py    # prompt | chat model | parser
│   │   ├── transcript_service.py    # YouTube transcripts
│   │   ├── recall_service.py        # proactive recall: related pages, no LLM
│   │   ├── query_log_service.py     # record of every question and answer
│   │   └── page_service.py          # status, list, forget
│   ├── utils/            # stateless helpers: llm (model factory), formatting,
│   │                     #   citations, scrub, urls, youtube, auth
│   ├── db.py             # data layer: schema, queue, keyword + vector search
│   ├── worker.py         # background loop draining the jobs table
│   ├── scripts/          # new_device, ingest_file, ask — thin
│   └── tests/            # pytest, on a throwaway database
```

Storage is one SQLite file (`%LOCALAPPDATA%\browser-memory\memory.db` by
default; set `MEMORY_DB_PATH` to move it). There is no database server and
nothing to migrate: `db.py` creates the schema on first start. Keyword search
uses SQLite's built-in FTS5 index; vector search is a numpy matrix product
over all chunk embeddings held in memory. The file lives outside the project
folder on purpose: the project is in OneDrive, and syncing a live database
file can corrupt it.

Placement rules:

* **Request path**: `router.py` → `orchestrator.py` → services. The router
  holds no logic.
* **The orchestrator owns the sequence.** Each flow (index a page, answer a
  question, find related pages) is one method written as numbered steps.
  It is the only file that calls services, and it also holds the small
  decisions between steps (when to abstain, how sources are numbered).
* **Services are independent.** Each does one job and imports no other
  service. When a stage needs another's output — retrieval needs the
  question's embedding, storage needs the chunks — the orchestrator fetches
  it and passes it in as an argument. `tests/test_architecture.py` enforces
  both rules.
* **Shared, stateless transforms** are module-level functions in `utils/`.
* **LLM calls live in one place**: `utils/llm.py` picks the chat model
  (Groq if `GROQ_API_KEY` is set, else Gemini); each chain is
  `prompt | model | StrOutputParser()` built once in its service.
* **Tunables live in `config.py`**, prompts in `prompts/` — never inline.
* **`db.py` is shared infrastructure** used by services, the worker and
  scripts. It is the only file that contains SQL.

## Run book

Server (Python 3.12, uv):

```sh
cd server
uv sync
cp .env.example .env       # fill GROQ_API_KEY or GOOGLE_API_KEY
uv run pytest              # only tests/test_api.py needs an LLM key
uv run uvicorn main:app --port 8000
```

Pair a device (prints a bearer token once):

```sh
uv run python scripts/new_device.py --label my-chrome
```

Extension (Node >= 20):

```sh
cd extension
npm install
npm run build              # or: npm run dev (auto-reloads)
```

Load it: Chrome → chrome://extensions → Developer mode → "Load unpacked" →
select `extension/.output/chrome-mv3`. Open the side panel via the toolbar
icon → Settings → paste server URL + device token → Save.

CLI equivalents (no extension needed):

```sh
uv run python scripts/ingest_file.py page.html --url https://example.com/x
uv run python scripts/ask.py "what did I read about X yesterday?"
```
