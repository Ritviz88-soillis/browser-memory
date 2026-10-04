// "You've read related things before" — shown under the current-page chip
// when memory holds pages close to the one open now. Renders nothing when
// there is no match, so an unrelated page stays quiet.

import { useState } from "react";
import { useActiveTab } from "../hooks/useActiveTab";
import { useRelated } from "../hooks/useRelated";

export default function RelatedPages() {
  const tab = useActiveTab();
  const pages = useRelated(tab?.url);
  const [open, setOpen] = useState(true);

  if (pages.length === 0) return null;

  return (
    <div className="related">
      <button className="related-head" onClick={() => setOpen(!open)}>
        <span>
          Related to {pages.length} {pages.length === 1 ? "page" : "pages"} you
          read before
        </span>
        <span className="caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul>
          {pages.map((p) => (
            <li key={p.id}>
              <a href={p.url} target="_blank" rel="noreferrer" title={p.snippet}>
                {p.title}
              </a>
              <span className="meta">
                {p.domain} · {p.visited.slice(0, 10)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
