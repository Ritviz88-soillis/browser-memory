// Tick open tabs to summarise or compare. The next question is then answered
// from those tabs only, each point cited to the tab it came from.
//
// Tabs that indexing would never touch (blocked sites, incognito, non-web
// pages) can't be ticked: their content never leaves the browser.

import { useEffect, useState } from "react";
import { urlPassesGates } from "@/utils/gates";
import { getSettings } from "@/utils/settings";

export const MAX_TABS = 5; // mirrors the server's MAX_COMPARE_TABS

interface Row {
  id: number;
  title: string;
  favIconUrl?: string;
  allowed: boolean;
}

interface Props {
  selected: number[];
  onChange: (tabIds: number[]) => void;
}

export default function TabPicker({ selected, onChange }: Props) {
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    let alive = true;

    async function refresh() {
      const settings = await getSettings();
      const tabs = await chrome.tabs.query({ currentWindow: true }).catch(() => []);
      if (!alive) return;
      const next = tabs
        .filter((t) => t.id != null && t.url)
        .map((t) => ({
          id: t.id!,
          title: t.title || t.url!,
          favIconUrl: t.favIconUrl,
          allowed:
            !t.incognito &&
            !settings.paused &&
            urlPassesGates(t.url!, settings.extraBlocked),
        }));
      setRows(next);
      // forget ticks for tabs that were closed or became private
      const stillValid = selected.filter((id) => next.some((r) => r.id === id && r.allowed));
      if (stillValid.length !== selected.length) onChange(stillValid);
    }

    void refresh();
    const onTabsChanged = () => void refresh();
    chrome.tabs.onUpdated.addListener(onTabsChanged);
    chrome.tabs.onRemoved.addListener(onTabsChanged);
    chrome.tabs.onCreated.addListener(onTabsChanged);
    return () => {
      alive = false;
      chrome.tabs.onUpdated.removeListener(onTabsChanged);
      chrome.tabs.onRemoved.removeListener(onTabsChanged);
      chrome.tabs.onCreated.removeListener(onTabsChanged);
    };
  }, [selected, onChange]);

  function toggle(id: number) {
    if (selected.includes(id)) onChange(selected.filter((s) => s !== id));
    else if (selected.length < MAX_TABS) onChange([...selected, id]);
  }

  const full = selected.length >= MAX_TABS;

  return (
    <div className="tab-picker">
      <div className="tab-picker-head">
        Tick tabs to summarise or compare (up to {MAX_TABS})
      </div>
      <ul>
        {rows.map((r) => {
          const ticked = selected.includes(r.id);
          return (
            <li key={r.id} className={r.allowed ? "" : "private"}>
              <label title={r.allowed ? r.title : "Private or blocked: never sent"}>
                <input
                  type="checkbox"
                  checked={ticked}
                  disabled={!r.allowed || (full && !ticked)}
                  onChange={() => toggle(r.id)}
                />
                {r.favIconUrl && <img src={r.favIconUrl} alt="" />}
                <span>{r.title}</span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
