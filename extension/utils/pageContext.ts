// Snapshot of a tab, attached to /ask so questions about an open page work
// even before the page is indexed.
//
// Same privacy rule as indexing: blocked/paused/incognito pages never leave
// the browser — the question is still asked, just without page context.

import { CurrentPage } from "./api";
import { requestExtraction } from "./extract";
import { urlPassesGates } from "./gates";
import { getSettings } from "./settings";

const TEXT_CAP = 8000;
const HTML_CAP = 500_000; // server accepts 600k; larger pages fall back to text

export async function getActiveTab(): Promise<chrome.tabs.Tab | null> {
  const [tab] = await chrome.tabs
    .query({ active: true, lastFocusedWindow: true })
    .catch(() => []);
  return tab ?? null;
}

// withHtml: include the article HTML so the server can cite exact passages
// (needed for answers; recall only needs the short text).
export async function getPageContext(
  tab: chrome.tabs.Tab,
  { withHtml = false } = {},
): Promise<CurrentPage | null> {
  if (!tab.id || !tab.url) return null;

  const settings = await getSettings();
  if (tab.incognito || settings.paused) return null;
  if (!urlPassesGates(tab.url, settings.extraBlocked)) return null;

  // Readability extraction via the content script, re-injecting if orphaned
  const resp = await requestExtraction(tab.id);

  let text: string | null = null;
  let html: string | null = null;
  if (resp?.ok && resp.html) {
    const doc = new DOMParser().parseFromString(resp.html, "text/html");
    text =
      (doc.body.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, TEXT_CAP) ||
      null;
    if (withHtml && resp.html.length <= HTML_CAP) html = resp.html;
  }

  return {
    url: tab.url,
    title: resp?.title ?? tab.title ?? null,
    text,
    html,
    tab_id: tab.id,
  };
}

export async function getCurrentPageContext(
  options: { withHtml?: boolean } = {},
): Promise<CurrentPage | null> {
  const tab = await getActiveTab();
  return tab ? getPageContext(tab, options) : null;
}
