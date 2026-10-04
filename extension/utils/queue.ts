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

let draining = false;

export async function drain(): Promise<void> {
  if (draining) return; // single-flight; concurrent calls are harmless no-ops
  draining = true;
  try {
    let items = await load();
    const remaining: QueueItem[] = [];
    for (const item of items) {
      try {
        await api.ingest(item.body); // server dedups on idempotency_key
      } catch {
        item.tries += 1;
        if (item.tries < MAX_TRIES) remaining.push(item);
        continue;
      }
    }
    await save(remaining);
  } finally {
    draining = false;
  }
}

export async function queueSize(): Promise<number> {
  return (await load()).length;
}
