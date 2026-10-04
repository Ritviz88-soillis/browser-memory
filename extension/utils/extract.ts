// Messaging a tab's content script.
//
// Every extension reload (each dev rebuild!) orphans content scripts in tabs
// that were already open — sendMessage then fails silently. When that happens
// we inject the script fresh and retry once. chrome:// and other uninjectable
// pages fail the injection and resolve to null.

export interface ExtractResponse {
  ok: boolean;
  html: string | null;
  title: string | null;
  lang: string | null;
  scrollDepth?: number;
}

export async function sendToContentScript<T>(
  tabId: number,
  message: unknown,
): Promise<T | null> {
  const ask = () =>
    chrome.tabs.sendMessage(tabId, message).catch(() => null) as Promise<T | null>;

  const first = await ask();
  if (first) return first;

  const injected = await chrome.scripting
    .executeScript({ target: { tabId }, files: ["content-scripts/content.js"] })
    .then(() => true)
    .catch(() => false);
  if (!injected) return null;

  return ask();
}

// Ask a tab for its Readability extraction; null means "nothing readable".
export function requestExtraction(tabId: number): Promise<ExtractResponse | null> {
  return sendToContentScript<ExtractResponse>(tabId, { type: "extract" });
}
