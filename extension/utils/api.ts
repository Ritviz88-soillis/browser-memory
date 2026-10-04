// Server client. Interfaces mirror server/schemas/; regenerate with
// `npm run gen:api` against a running server when the contract changes.

import { getSettings } from "./settings";

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

export interface SourceOut {
  n: number;
  title: string;
  url: string;
  domain: string;
  heading_path: string[];
  visited: string;
  snippet: string;
  passage: string; // the exact text cited, for highlighting on the page
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { serverUrl, token } = await getSettings();
  const resp = await fetch(serverUrl.replace(/\/$/, "") + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });
  if (!resp.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${resp.status}`);
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
  ask: (
    question: string,
    history: HistoryTurn[] = [],
    currentPage: CurrentPage | null = null,
  ) =>
    request<AskOut>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, history, current_page: currentPage }),
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
