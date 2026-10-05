// Server client. Interfaces mirror server/schemas/; regenerate with
// `npm run gen:api` against a running server when the contract changes.

import { getSettings, saveSettings } from "./settings";

export interface VisitIn {
  started_at: string;
  last_active_at?: string | null;
  dwell_ms?: number;
  scroll_depth_pct?: number | null;
  referrer_url?: string | null;
  tab_id?: number | null;
  source?: "navigation" | "history_state" | "reactivation";
}

export interface IngestIn {
  idempotency_key: string;
  url: string;
  title?: string | null;
  lang?: string | null;
  html: string;
  visit: VisitIn;
  model?: string;
}

// A PDF the user is reading: the file is sent and read on the server,
// because an extension cannot read text out of the browser's PDF viewer.
export interface IngestPdfIn {
  idempotency_key: string;
  url: string;
  title?: string | null;
  pdf_base64: string;
  visit: VisitIn;
}

// A failed request, with the HTTP status so callers can tell "try again
// later" (server down) from "this will never work" (422: not a readable PDF).
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export interface SourceOut {
  n: number;
  title: string;
  url: string;
  domain: string;
  heading_path: string[];
  visited: string;
  snippet: string;
  passage: string; // the exact text cited, for highlighting on the page
  highlights: string[]; // the sentences in it that support the answer
  live: boolean; // from a tab open right now
  tab_id: number | null;
}

export interface AskOut {
  answer: string;
  abstained: boolean;
  sources: SourceOut[];
  filters: {
    semantic_query: string;
    since: string | null;
    until: string | null;
    domains: string[] | null;
  };
  latency_ms: number;
}

export interface PageOut {
  id: string;
  url: string;
  domain: string;
  title: string | null;
  word_count: number;
  last_visited_at: string;
  indexed_at: string;
}

// Pairing: the extension asks the server for its own access token, so the
// user never copies one. The server only answers a browser extension (it
// checks the request's Origin, which a web page cannot forge).
let pairing: Promise<string> | null = null;

async function pair(serverUrl: string): Promise<string> {
  // single-flight: several requests starting together share one pairing
  pairing ??= (async () => {
    const resp = await fetch(serverUrl.replace(/\/$/, "") + "/pair", { method: "POST" });
    if (!resp.ok) throw new Error(`POST /pair -> ${resp.status}`);
    const { token } = (await resp.json()) as { token: string };
    await saveSettings({ token });
    return token;
  })().finally(() => {
    pairing = null;
  });
  return pairing;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { serverUrl, token: saved } = await getSettings();
  let token = saved || (await pair(serverUrl));

  const send = () =>
    fetch(serverUrl.replace(/\/$/, "") + path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...init?.headers,
      },
    });

  let resp = await send();
  if (resp.status === 401) {
    // the saved token belongs to an old or reset server: pair again, once
    token = await pair(serverUrl);
    resp = await send();
  }
  if (!resp.ok) {
    // the server explains failures the user can act on (e.g. the language
    // model's free limit being used up); show that instead of a status code
    const detail = await resp
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    throw new ApiError(
      typeof detail === "string" ? detail : `${init?.method ?? "GET"} ${path} -> ${resp.status}`,
      resp.status,
    );
  }
  return resp.json() as Promise<T>;
}

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface CurrentPage {
  url: string;
  title?: string | null;
  text?: string | null;
  html?: string | null; // Readability article HTML, so answers cite exact passages
  tab_id?: number | null;
}

export interface RelatedPage {
  id: string;
  title: string;
  url: string;
  domain: string;
  visited: string;
  snippet: string;
  similarity: number;
}

export interface StatusOut {
  pending_jobs: number;
  failed_jobs: number;
  pages: number;
  chunks: number;
}

export const api = {
  ingest: (body: IngestIn) =>
    request<{ queued: boolean; duplicate: boolean }>("/ingest", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  ingestPdf: (body: IngestPdfIn) =>
    request<{ queued: boolean; duplicate: boolean }>("/ingest/pdf", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  ask: (
    question: string,
    history: HistoryTurn[] = [],
    currentPage: CurrentPage | null = null,
    tabs: CurrentPage[] = [], // ticked tabs: answer from these only
  ) =>
    request<AskOut>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, history, current_page: currentPage, tabs }),
    }),
  related: (page: CurrentPage) =>
    request<{ pages: RelatedPage[] }>("/related", {
      method: "POST",
      body: JSON.stringify(page),
    }),
  status: () => request<StatusOut>("/status"),
  pages: (q?: string) =>
    request<PageOut[]>("/pages" + (q ? `?q=${encodeURIComponent(q)}` : "")),
  forgetPage: (id: string) =>
    request<{ deleted: number }>(`/pages/${id}`, { method: "DELETE" }),
  forgetSite: (domain: string) =>
    request<{ deleted: number }>(`/sites/${encodeURIComponent(domain)}`, {
      method: "DELETE",
    }),
  health: () => request<{ ok: boolean }>("/health"),
};
