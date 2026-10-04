// Tracks the tab the user is actually looking at, live. The side panel is a
// real extension page, so it can use chrome.tabs directly — no round-trip
// through the (possibly asleep) background worker.

import { useEffect, useState } from "react";

export interface ActiveTab {
  url: string;
  title: string;
  favIconUrl?: string;
}

export function useActiveTab(): ActiveTab | null {
  const [tab, setTab] = useState<ActiveTab | null>(null);

  useEffect(() => {
    let alive = true;

    async function refresh() {
      const [t] = await chrome.tabs
        .query({ active: true, lastFocusedWindow: true })
        .catch(() => []);
      if (!alive) return;
      setTab(
        t?.url
          ? { url: t.url, title: t.title || t.url, favIconUrl: t.favIconUrl }
          : null,
      );
    }

    void refresh();
    const onChange = () => void refresh();
    const onUpdated = (_id: number, info: chrome.tabs.TabChangeInfo) => {
      if (info.url || info.title || info.favIconUrl || info.status === "complete")
        void refresh();
    };
    chrome.tabs.onActivated.addListener(onChange);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.windows.onFocusChanged.addListener(onChange);
    return () => {
      alive = false;
      chrome.tabs.onActivated.removeListener(onChange);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      chrome.windows.onFocusChanged.removeListener(onChange);
    };
  }, []);

  return tab;
}
