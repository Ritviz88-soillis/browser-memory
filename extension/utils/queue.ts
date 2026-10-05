// Persistent upload queue in chrome.storage.local.
//
// MV3 kills the service worker after ~30s idle, so the queue must survive
// termination: every mutation is written back to storage immediately, and the
// drain runs both opportunistically (on enqueue) and from the heartbeat alarm
// so items stranded by a kill are retried.

import { api, ApiError, IngestIn, VisitIn } from "./api";

const KEY = "ingest_queue";
const MAX_QUEUE = 100;
const MAX_TRIES = 5;
const PDF_MAX_BYTES = 20_000_000; // mirrors the server's PDF_MAX_BYTES

// A PDF waits in the queue as a reference only: the file is downloaded at
// upload time, because files are far too large for extension storage.
export interface PdfRef {
  kind: "pdf";
  idempotency_key: string;
  url: string;
  title?: string | null;
  visit: VisitIn;
}

interface QueueItem {
  body: IngestIn | PdfRef;
  tries: number;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  // in pieces: one call with millions of arguments overflows the stack
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

// Download the PDF (with the user's cookies, so files behind a login work)
// and hand it to the server. Returns false when the URL turns out not to be
// a PDF, or is too large: there is nothing to index and nothing to retry.
async function uploadPdf(ref: PdfRef): Promise<boolean> {
  const resp = await fetch(ref.url, { credentials: "include" });
  if (!resp.ok) throw new Error(`could not download ${ref.url}: ${resp.status}`);
  if (!(resp.headers.get("content-type") ?? "").includes("pdf")) return false;

  const file = await resp.arrayBuffer();
  if (file.byteLength > PDF_MAX_BYTES) return false;

  await api.ingestPdf({
    idempotency_key: ref.idempotency_key,
    url: ref.url,
    title: ref.title,
    pdf_base64: toBase64(file),
    visit: ref.visit,
  });
  return true;
}

async function load(): Promise<QueueItem[]> {
  const stored = await chrome.storage.local.get(KEY);
  return stored[KEY] ?? [];
}

async function save(items: QueueItem[]): Promise<void> {
  await chrome.storage.local.set({ [KEY]: items.slice(-MAX_QUEUE) });
}

export async function enqueue(body: IngestIn | PdfRef): Promise<void> {
  const items = await load();
  items.push({ body, tries: 0 });
  await save(items);
  void drain();
}

// Message broadcast when a page has been handed to the server for indexing.
export const PAGE_SENT = "page-sent";

// Run `callback` shortly after each page is sent: the server needs about a
// second to index it (longer for its first page), so look a few times.
export function onPageSent(callback: (url: string) => void): () => void {
  const timers: ReturnType<typeof setTimeout>[] = [];
  const listener = (message: { type?: string; url?: string }) => {
    if (message?.type !== PAGE_SENT || !message.url) return;
    const url = message.url;
    for (const delay of [800, 2500, 7000]) {
      timers.push(setTimeout(() => callback(url), delay));
    }
  };
  chrome.runtime.onMessage.addListener(listener);
  return () => {
    chrome.runtime.onMessage.removeListener(listener);
    timers.forEach(clearTimeout);
  };
}

let draining = false;

export async function drain(): Promise<void> {
  if (draining) return; // single-flight; concurrent calls are harmless no-ops
  draining = true;
  try {
    const items = await load();
    const remaining: QueueItem[] = [];
    for (const item of items) {
      try {
        // the server dedups on idempotency_key, so a retry is harmless
        const sent = "kind" in item.body ? await uploadPdf(item.body) : (await api.ingest(item.body), true);
        if (sent) {
          // tell the side panel (if open) so it refreshes without waiting
          chrome.runtime
            .sendMessage({ type: PAGE_SENT, url: item.body.url })
            .catch(() => {});
        }
      } catch (e) {
        // 422: the server read the file and it has no usable text (a
        // scanned PDF, say). Retrying cannot change that.
        if (e instanceof ApiError && e.status === 422) continue;
        item.tries += 1;
        if (item.tries < MAX_TRIES) remaining.push(item);
        continue;
      }
    }
    // keep anything queued while this drain was uploading
    const queuedMeanwhile = (await load()).slice(items.length);
    await save([...remaining, ...queuedMeanwhile]);
  } finally {
    draining = false;
  }
}

export async function queueSize(): Promise<number> {
  return (await load()).length;
}
