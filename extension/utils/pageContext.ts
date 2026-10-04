// Snapshot of the tab open next to the side panel, attached to /ask so
// "what is this page about?" works even before the page is indexed.
//
// Same privacy rule as indexing: blocked/paused/incognito pages never leave
// the browser — the question is still asked, just without page context.

import { CurrentPage } from "./api";
import { requestExtraction } from "./extract";
import { urlPassesGates } from "./gates";
import { getSettings } from "./settings";

const TEXT_CAP = 8000;

export async function getCurrentPageContext(): Promise<CurrentPage | null> {
  const [tab] = await chrome.tabs
    .query({ active: true, lastFocusedWindow: true })
    .catch(() => []);
  if (!tab?.id || !tab.url) return null;

  const settings = await getSettings();
  if (tab.incognito || settings.paused) return null;
  if (!urlPassesGates(tab.url, settings.extraBlocked)) return null;

  // Readability extraction via the content script, re-injecting if orphaned
  const resp = await requestExtraction(tab.id);

  let text: string | null = null;
  if (resp?.ok && resp.html) {
    const doc = new DOMParser().parseFromString(resp.html, "text/html");
    text =
      (doc.body.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, TEXT_CAP) ||
      null;
  }

  return { url: tab.url, title: resp?.title ?? tab.title ?? null, text };
}
