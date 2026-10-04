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
│   ├── orchestrator.py   # MemoryOrchestrator: thin delegate to the services
│   ├── schemas/          # Pydantic request/response models (the wire contract)
│   ├── prompts/          # answer_prompt.py, query_filter_prompt.py
│   ├── services/
│   │   ├── rag_service.py           # THE BRAIN for asking: filter → retrieve → generate
│   │   ├── ingestion_service.py     # THE BRAIN for indexing: scrub → chunk → embed → store
│   │   ├── chunking_service.py      # stage: heading-aware chunks with overlap
│   │   ├── embedding_service.py     # stage: local bge-small / Jina embeddings
│   │   ├── query_filter_service.py  # stage: date + site filters from the question
│   │   ├── retrieval_service.py     # stage: hybrid (vector + keyword) search
│   │   ├── generation_service.py    # stage: prompt | chat model | parser
│   │   ├── transcript_service.py    # YouTube transcripts for the open tab
│   │   ├── recall_service.py        # proactive recall: related pages, no LLM
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

Placement rules (same as the t1 module and naive-rag):

* **Request path**: `router.py` → `orchestrator.py` → a brain service →
  stage services. The router and orchestrator hold no logic.
* **A whole stage algorithm** gets its own class in `services/`. The two
  brain services (`rag_service`, `ingestion_service`) only sequence stages
  and apply policy (when to abstain, what gets logged).
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
