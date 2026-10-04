// Proactive recall: pages read before that relate to the tab open now.
//
// Runs only while the side panel is showing (the hook is mounted), waits for
// the tab to settle before asking, and remembers the answer per URL so
// flicking between tabs costs nothing.

import { useEffect, useState } from "react";
import { api, RelatedPage } from "@/utils/api";
import { getCurrentPageContext } from "@/utils/pageContext";

const SETTLE_MS = 1000;
const CACHE_TTL_MS = 5 * 60_000;
const CACHE_MAX = 50;

const cache = new Map<string, { at: number; pages: RelatedPage[] }>();

export function useRelated(url: string | undefined): RelatedPage[] {
  const [pages, setPages] = useState<RelatedPage[]>([]);

  useEffect(() => {
    setPages([]);
    if (!url) return;

    const hit = cache.get(url);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
      setPages(hit.pages);
      return;
    }

    let alive = true;
    const timer = setTimeout(async () => {
      try {
        // null when the page is blocked, paused or incognito: never sent
        const page = await getCurrentPageContext();
        if (!alive || !page || page.url !== url) return;

        const result = await api.related(page);
        if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value!);
        cache.set(url, { at: Date.now(), pages: result.pages });
        if (alive) setPages(result.pages);
      } catch {
        // server offline or not paired: recall is optional, stay quiet
      }
    }, SETTLE_MS);

    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [url]);

  return pages;
}
