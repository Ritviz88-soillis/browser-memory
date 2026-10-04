// Ask a tab's content script for its Readability extraction.
//
// Every extension reload (each dev rebuild!) orphans content scripts in tabs
// that were already open — sendMessage then fails silently. When that happens
// we inject the script fresh and retry once. chrome:// and other uninjectable
// pages fail the injection and resolve to null, which callers treat as
// "nothing readable".

export interface ExtractResponse {
  ok: boolean;
  html: string | null;
  title: string | null;
  lang: string | null;
  scrollDepth?: number;
}

export async function requestExtraction(
  tabId: number,
): Promise<ExtractResponse | null> {
  const ask = () =>
    chrome.tabs
      .sendMessage(tabId, { type: "extract" })
      .catch(() => null) as Promise<ExtractResponse | null>;

  let resp = await ask();
  if (resp) return resp;

  const injected = await chrome.scripting
    .executeScript({ target: { tabId }, files: ["content-scripts/content.js"] })
    .then(() => true)
    .catch(() => false);
  if (!injected) return null;

  resp = await ask();
  return resp;
}
