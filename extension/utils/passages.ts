// Side-panel side of passage highlighting: find (or open) the tab that shows
// a cited page, then ask its content script to highlight the passages.

import type { SourceOut } from "./api";
import { sendToContentScript } from "./extract";
import type { PassageSpec } from "./highlight";

// What to mark on the page for one cited source: its passage, and the
// sentences in it that support the answer. Answers saved before passages
// existed only carry the short snippet.
export function passageOf(source: SourceOut): PassageSpec {
  return { text: source.passage ?? source.snippet, key: source.highlights ?? [] };
}

const TRACKING_PREFIXES = ["utm_", "fbclid", "gclid", "ref_", "mc_"];
const LOAD_TIMEOUT_MS = 15_000;

export interface HighlightResult {
  found: number; // passages located on the page
  total: number;
}

// Same identity the server uses for a page: no fragment, no tracking params.
export function pageKey(rawUrl: string): string {
  try {
    const url = new URL(rawUrl);
    const query = url.search
      .slice(1)
      .split("&")
      .filter((p) => p && !TRACKING_PREFIXES.some((t) => p.toLowerCase().startsWith(t)))
      .join("&");
    return `${url.protocol}//${url.host.toLowerCase()}${url.pathname || "/"}${query ? "?" + query : ""}`;
  } catch {
    return rawUrl;
  }
}

async function findTab(url: string, tabId?: number | null): Promise<chrome.tabs.Tab | null> {
  const key = pageKey(url);
  if (tabId != null) {
    // the tab may have navigated elsewhere since the answer was given
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (tab?.url && pageKey(tab.url) === key) return tab;
  }
  const tabs = await chrome.tabs.query({}).catch(() => []);
  return tabs.find((t) => t.url && pageKey(t.url) === key) ?? null;
}

function waitUntilLoaded(tabId: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve();
    };
    const listener = (id: number, info: chrome.tabs.TabChangeInfo) => {
      if (id === tabId && info.status === "complete") done();
    };
    const timer = setTimeout(done, LOAD_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// Highlight passages on the page at `url`.
//   bringToFront: switch to that tab, opening the page if it isn't open.
//   Without it, only an already-open tab is highlighted, silently.
// Returns null when there is no tab to highlight in, or the page can't be
// scripted (chrome:// pages, the Web Store, PDFs).
export async function showPassages(
  url: string,
  passages: PassageSpec[],
  { tabId = null as number | null, bringToFront = true } = {},
): Promise<HighlightResult | null> {
  let tab = await findTab(url, tabId);

  if (!tab) {
    if (!bringToFront) return null;
    tab = await chrome.tabs.create({ url, active: true });
    if (!tab.id) return null;
    await waitUntilLoaded(tab.id);
  } else if (bringToFront && tab.id) {
    await chrome.tabs.update(tab.id, { active: true });
    if (tab.windowId != null) await chrome.windows.update(tab.windowId, { focused: true });
  }

  if (!tab.id) return null;
  return sendToContentScript<HighlightResult>(tab.id, { type: "highlight", passages });
}

export async function clearPassages(tabId: number): Promise<void> {
  await sendToContentScript(tabId, { type: "clear-highlight" });
}
