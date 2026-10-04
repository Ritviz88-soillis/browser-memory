// Content script: extracts readable article HTML on request, and highlights
// cited passages in the page.
//
// Readability MUTATES the document it parses — always run it on a clone or
// the user's live page gets visibly destroyed.

import { Readability } from "@mozilla/readability";
import { clearHighlights, highlightPassages } from "@/utils/highlight";

export default defineContentScript({
  matches: ["http://*/*", "https://*/*"],
  runAt: "document_idle",
  main() {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      try {
        if (msg?.type === "extract") {
          const clone = document.cloneNode(true) as Document;
          const article = new Readability(clone).parse();

          const scrolled = window.scrollY + window.innerHeight;
          const height = Math.max(document.body.scrollHeight, 1);
          const scrollDepth = Math.min(100, Math.round((scrolled / height) * 100));

          sendResponse({
            ok: true,
            html: article?.content ?? null,
            title: article?.title || document.title || null,
            lang: document.documentElement.lang || null,
            scrollDepth,
          });
        } else if (msg?.type === "highlight") {
          sendResponse({ ok: true, ...highlightPassages(msg.passages ?? []) });
        } else if (msg?.type === "clear-highlight") {
          clearHighlights();
          sendResponse({ ok: true });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
      // listener returns synchronously; response already sent
    });
  },
});
