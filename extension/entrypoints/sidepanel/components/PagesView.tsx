import { useEffect, useState } from "react";
import { api, PageOut } from "@/utils/api";

export default function PagesView() {
  const [pages, setPages] = useState<PageOut[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");

  async function refresh(query = q) {
    try {
      setPages(await api.pages(query || undefined));
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="pages">
      <div className="composer">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && refresh()}
          placeholder="Filter indexed pages…"
        />
        <button onClick={() => refresh()}>Search</button>
      </div>
      {error && <div className="err">{error}</div>}
      <ul className="page-list">
        {pages.map((p) => (
          <li key={p.id}>
            <a href={p.url} target="_blank" rel="noreferrer">
              {p.title ?? p.url}
            </a>
            <span className="meta">
              {p.domain} · {p.last_visited_at.slice(0, 10)} · {p.word_count}w
            </span>
            <span className="actions">
              <button
                onClick={async () => {
                  await api.forgetPage(p.id);
                  void refresh();
                }}
              >
                forget
              </button>
              <button
                onClick={async () => {
                  if (confirm(`Forget and block ${p.domain}?`)) {
                    await api.forgetSite(p.domain);
                    void refresh();
                  }
                }}
              >
                block site
              </button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
