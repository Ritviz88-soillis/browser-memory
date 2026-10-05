import { useState } from "react";
import { SourceOut } from "@/utils/api";
import { showPassages } from "@/utils/passages";
import { ChatMessage } from "../hooks/useChat";

// Content renders as PLAIN TEXT, deliberately. The answer contains retrieved
// page text; innerHTML-style rendering would let a malicious page inject
// markup that executes inside the extension. Citations become buttons by
// splitting the string — still only React text nodes and elements we create.
export default function MessageBubble({ message }: { message: ChatMessage }) {
  // source numbers whose passage could not be found on the page
  const [missing, setMissing] = useState<number[]>([]);

  if (message.role === "user") {
    return (
      <div className="row user">
        <div className="bubble user">{message.content}</div>
      </div>
    );
  }

  const r = message.result;
  const abstained = r?.abstained;
  const sources = new Map((r?.sources ?? []).map((s) => [s.n, s]));

  // Jump to the cited passage: switch to (or open) its page and highlight it.
  async function show(source: SourceOut) {
    // answers saved before passages existed only carry the short snippet
    const result = await showPassages(source.url, [source.passage ?? source.snippet], {
      tabId: source.tab_id,
    }).catch(() => null);
    setMissing((m) =>
      result && result.found > 0 ? m.filter((n) => n !== source.n) : [...m, source.n],
    );
  }

  return (
    <div className="row">
      <div className={`bubble assistant${message.error ? " error" : ""}${abstained ? " abstained" : ""}`}>
        {message.pending ? (
          <span className="dots">
            <i /> <i /> <i />
          </span>
        ) : (
          <>
            {r && <FilterChips f={r.filters} />}
            {message.note && <div className="chips">{message.note}</div>}
            <p>
              {message.content.split(/(\[\d{1,3}\])/).map((part, i) => {
                const source = sources.get(Number(part.slice(1, -1)));
                return /^\[\d{1,3}\]$/.test(part) && source ? (
                  <button
                    key={i}
                    className="cite"
                    title="Show this passage on the page"
                    onClick={() => void show(source)}
                  >
                    {part}
                  </button>
                ) : (
                  part
                );
              })}
            </p>
            {r && r.sources.length > 0 && (
              <ol className="sources">
                {r.sources.map((s) => (
                  <li key={s.n} value={s.n}>
                    <a href={s.url} target="_blank" rel="noreferrer">
                      {s.title}
                    </a>
                    <span className="meta">
                      {s.domain} · {s.live ? "open tab" : s.visited.slice(0, 10)}
                    </span>
                    {s.heading_path.length > 0 && (
                      <span className="where">§ {s.heading_path.join(" › ")}</span>
                    )}
                    <button className="show" onClick={() => void show(s)}>
                      show passage
                    </button>
                    {missing.includes(s.n) && (
                      <span className="where">
                        couldn't find this passage on the page (it may have changed)
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function FilterChips({ f }: { f: NonNullable<ChatMessage["result"]>["filters"] }) {
  const bits: string[] = [];
  if (f.since) bits.push(`since ${f.since.slice(0, 10)}`);
  if (f.until) bits.push(`until ${f.until.slice(0, 10)}`);
  if (f.domains?.length) bits.push(f.domains.join(", "));
  if (!bits.length) return null;
  return <div className="chips">{bits.join(" · ")}</div>;
}
