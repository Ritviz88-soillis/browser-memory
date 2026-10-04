import { useEffect, useState } from "react";
import { api, StatusOut } from "@/utils/api";
import ChatView from "./components/ChatView";
import PagesView from "./components/PagesView";
import SettingsView from "./components/SettingsView";

type Tab = "ask" | "pages" | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("ask");
  const [status, setStatus] = useState<StatusOut | null>(null);

  // Live indexing status while the panel is open. Fast only while a page is
  // being indexed; otherwise slow, so an idle panel doesn't keep the hosted
  // database awake around the clock.
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let busy = false;
      try {
        const s = await api.status();
        busy = s.pending_jobs > 0;
        if (alive) setStatus(s);
      } catch {
        if (alive) setStatus(null);
      }
      if (alive) timer = setTimeout(poll, busy ? 5000 : 30000);
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  return (
    <div className="app">
      <header>
        <span className="brand">Browser Memory</span>
        {status && (
          <span className="status">
            <i className="dot green" /> {status.pages} indexed
            {status.pending_jobs > 0 && (
              <>
                {" "}
                <i className="dot yellow" /> {status.pending_jobs} reading
              </>
            )}
          </span>
        )}
      </header>
      <nav>
        {(["ask", "pages", "settings"] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      {tab === "ask" && <ChatView />}
      {tab === "pages" && <PagesView />}
      {tab === "settings" && <SettingsView />}
    </div>
  );
}
