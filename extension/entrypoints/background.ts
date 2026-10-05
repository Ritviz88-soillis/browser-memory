// Background service worker.
//
// MV3 terminates this worker after ~30s idle, so NO state lives in module
// variables: navigation candidates go to chrome.storage.session (survives SW
// restarts, cleared when the browser closes), the upload queue to
// chrome.storage.local, and a heartbeat alarm re-drives everything a kill
// interrupted.

import { requestExtraction } from "@/utils/extract";
import { urlPassesGates } from "@/utils/gates";
import { getSettings } from "@/utils/settings";
import { drain, enqueue } from "@/utils/queue";

const DWELL_MS = 10_000; // minimum reading time before a page is indexed
const CANDIDATES_KEY = "nav_candidates"; // storage.session
const HASHES_KEY = "recent_hashes"; // storage.local, LRU
const HASH_LRU = 300;

interface Candidate {
  url: string;
  at: number;
  source: "navigation" | "history_state" | "reactivation";
  referrer?: string;
}

export default defineBackground(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => {});

  chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create("heartbeat", { periodInMinutes: 0.5 });
    void seedOpenTabs();
  });

  // session-restored tabs never fire onUpdated "complete" — sweep them here
  chrome.runtime.onStartup.addListener(() => void seedOpenTabs());

  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "heartbeat") {
      void checkCandidates();
      void drain();
    }
  });

  chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
    if (info.status === "complete" && tab.url) {
      void noteCandidate(tabId, tab.url, "navigation");
    }
  });

  // SPA route changes never fire tabs.onUpdated with a new load
  chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
    if (details.frameId === 0) {
      void noteCandidate(details.tabId, details.url, "history_state");
    }
  });

  chrome.tabs.onRemoved.addListener((tabId) => {
    void dropCandidate(tabId);
  });

  // a background tab only starts earning dwell once the user looks at it
  chrome.tabs.onActivated.addListener((activeInfo) => {
    void handleActivated(activeInfo.tabId);
  });
});

async function handleActivated(tabId: number): Promise<void> {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab?.url) await noteCandidate(tabId, tab.url, "reactivation");
  await checkCandidates();
}

// One candidate per window's visible tab; everything else waits for activation.
async function seedOpenTabs(): Promise<void> {
  const tabs = await chrome.tabs.query({ active: true }).catch(() => []);
  for (const tab of tabs) {
    if (tab.id && tab.url) await noteCandidate(tab.id, tab.url, "reactivation");
  }
}

async function loadCandidates(): Promise<Record<number, Candidate>> {
  const stored = await chrome.storage.session.get(CANDIDATES_KEY);
  return stored[CANDIDATES_KEY] ?? {};
}

async function saveCandidates(c: Record<number, Candidate>): Promise<void> {
  await chrome.storage.session.set({ [CANDIDATES_KEY]: c });
}

async function noteCandidate(
  tabId: number,
  url: string,
  source: Candidate["source"],
): Promise<void> {
  const settings = await getSettings();
  // no token check: the first upload pairs with the server automatically
  if (settings.paused) return;

  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || tab.incognito) return; // incognito: never, no exceptions
  if (!tab.active) return; // dwell = time on screen, not time since load
  if (!urlPassesGates(url, settings.extraBlocked)) return;

  const candidates = await loadCandidates();
  // URL change resets the dwell clock; same URL keeps the original timestamp
  if (candidates[tabId]?.url !== url) {
    candidates[tabId] = { url, at: Date.now(), source };
    await saveCandidates(candidates);
    // Check the moment the reading time is up. The worker stays awake for
    // 30 s after an event, so this timer fires; the heartbeat alarm remains
    // as the safety net for a worker that was killed in between.
    setTimeout(() => void checkCandidates(), DWELL_MS + 300);
  }
}

async function dropCandidate(tabId: number): Promise<void> {
  const candidates = await loadCandidates();
  if (candidates[tabId]) {
    delete candidates[tabId];
    await saveCandidates(candidates);
  }
}

async function checkCandidates(): Promise<void> {
  const candidates = await loadCandidates();
  const now = Date.now();

  for (const [tabIdStr, cand] of Object.entries(candidates)) {
    const tabId = Number(tabIdStr);
    if (now - cand.at < DWELL_MS) continue;

    const tab = await chrome.tabs.get(tabId).catch(() => null);
    // no longer visible: drop; reactivation starts a fresh dwell clock
    if (!tab || tab.url !== cand.url || tab.discarded || !tab.active) {
      await dropCandidate(tabId);
      continue;
    }

    await dropCandidate(tabId); // claim before extracting: no double-index
    await extractAndQueue(tabId, cand, now - cand.at);
  }
}

async function extractAndQueue(
  tabId: number,
  cand: Candidate,
  dwellMs: number,
): Promise<void> {
  const resp = await requestExtraction(tabId);
  const visit = {
    started_at: new Date(cand.at).toISOString(),
    dwell_ms: Math.min(dwellMs, 3_600_000),
    scroll_depth_pct: resp?.scrollDepth ?? null,
    referrer_url: cand.referrer ?? null,
    tab_id: tabId,
    source: cand.source,
  };

  if (!resp) {
    // The tab cannot be scripted at all. On a web URL that almost always
    // means the browser's PDF viewer, which extensions cannot read. Queue a
    // reference: the file is downloaded and its type checked at upload time,
    // so anything that is not a PDF is dropped there.
    if (await alreadySeen(`pdf:${cand.url}`)) return;
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    await enqueue({
      kind: "pdf",
      idempotency_key: crypto.randomUUID(),
      url: cand.url,
      title: tab?.title ?? null,
      visit,
    });
    return;
  }
  if (!resp.ok || !resp.html) return; // a page with nothing readable

  // content-hash dedup: a revisit of unchanged content costs one hash lookup
  if (await alreadySeen(await sha256Hex(resp.html))) return;

  await enqueue({
    idempotency_key: crypto.randomUUID(),
    url: cand.url,
    title: resp.title,
    lang: resp.lang,
    html: resp.html,
    visit,
  });
}

// True if this content (by fingerprint) was already sent; otherwise records it.
async function alreadySeen(fingerprint: string): Promise<boolean> {
  const hashes: string[] =
    (await chrome.storage.local.get(HASHES_KEY))[HASHES_KEY] ?? [];
  if (hashes.includes(fingerprint)) return true;
  await chrome.storage.local.set({
    [HASHES_KEY]: [...hashes.slice(-HASH_LRU + 1), fingerprint],
  });
  return false;
}

async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(buf), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
}
