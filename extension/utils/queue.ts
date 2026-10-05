// Persistent upload queue in chrome.storage.local.
//
// MV3 kills the service worker after ~30s idle, so the queue must survive
// termination: every mutation is written back to storage immediately, and the
// drain runs both opportunistically (on enqueue) and from the heartbeat alarm
// so items stranded by a kill are retried.

import { api, IngestIn } from "./api";

const KEY = "ingest_queue";
const MAX_QUEUE = 100;
const MAX_TRIES = 5;

interface QueueItem {
  body: IngestIn;
  tries: number;
}

async function load(): Promise<QueueItem[]> {
  const stored = await chrome.storage.local.get(KEY);
  return stored[KEY] ?? [];
}

async function save(items: QueueItem[]): Promise<void> {
  await chrome.storage.local.set({ [KEY]: items.slice(-MAX_QUEUE) });
}

export async function enqueue(body: IngestIn): Promise<void> {
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
        await api.ingest(item.body); // server dedups on idempotency_key
        // tell the side panel (if open) so it refreshes without waiting
        chrome.runtime
          .sendMessage({ type: PAGE_SENT, url: item.body.url })
          .catch(() => {});
      } catch {
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
