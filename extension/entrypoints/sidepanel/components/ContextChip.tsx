// "You are here" chip above the chat: shows the page the user is on and
// whether it is in memory. Mirrors the gates the background worker applies,
// so a blocked page honestly says it will never be indexed.

import { useEffect, useState } from "react";
import { api } from "@/utils/api";
import { urlPassesGates } from "@/utils/gates";
import { pageKey } from "@/utils/passages";
import { onPageSent } from "@/utils/queue";
import { getSettings } from "@/utils/settings";
import { useActiveTab } from "../hooks/useActiveTab";

type IndexState = "indexed" | "pending" | "blocked";

export default function ContextChip() {
  const tab = useActiveTab();
  const [state, setState] = useState<IndexState | null>(null);

  useEffect(() => {
    setState(null);
    if (!tab) return;
    const url = tab.url;
    let alive = true;

    async function check() {
      const settings = await getSettings();
      if (settings.paused || !urlPassesGates(url, settings.extraBlocked)) {
        if (alive) setState("blocked");
        return;
      }
      try {
        // the hostname narrows the list; the page's identity (URL without
        // fragment or tracking parameters, as the server stores it) decides
        const pages = await api.pages(new URL(url).hostname);
        const key = pageKey(url);
        if (alive) setState(pages.some((p) => pageKey(p.url) === key) ? "indexed" : "pending");
      } catch {
        if (alive) setState("pending");
      }
    }

    // debounce: rapid tab switching shouldn't spam the server
    const t = setTimeout(() => void check(), 300);
    // flip to "in memory" as soon as this page has been indexed
    const stopListening = onPageSent((sentUrl) => {
      if (pageKey(sentUrl) === pageKey(url)) void check();
    });
    return () => {
      alive = false;
      clearTimeout(t);
      stopListening();
    };
  }, [tab?.url]);

  if (!tab) return null;

  let domain = "";
  try {
    domain = new URL(tab.url).hostname;
  } catch {
    /* chrome:// etc. */
  }

  return (
    <div className="context-chip" title={tab.url}>
      {tab.favIconUrl && <img src={tab.favIconUrl} alt="" />}
      <span className="title">{tab.title}</span>
      {domain && <span className="domain">{domain}</span>}
      {state === "indexed" && <span className="badge in">in memory</span>}
      {state === "pending" && <span className="badge">saved after 10 s of reading</span>}
      {state === "blocked" && <span className="badge off">not indexed</span>}
    </div>
  );
}
