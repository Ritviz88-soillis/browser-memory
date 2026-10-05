import { useState } from "react";
import { SourceOut } from "@/utils/api";
import { pageKey, passageOf, showPassages } from "@/utils/passages";
import { ChatMessage } from "../hooks/useChat";
import AnswerText from "./AnswerText";

interface PageGroup {
  key: string;
  title: string;
  url: string;
  domain: string;
  live: boolean;
  visited: string;
  sources: SourceOut[];
}

// One entry per page, holding the passages cited from it. An answer often
// cites several passages of the same page; listing the page once is easier
// to read, and when tabs are compared it shows what came from which tab.
function groupByPage(sources: SourceOut[]): PageGroup[] {
  const groups = new Map<string, PageGroup>();
  for (const source of sources) {
    const key = `${source.tab_id ?? ""}|${pageKey(source.url)}`;
    const group = groups.get(key) ?? {
      key,
      title: source.title,
      url: source.url,
      domain: source.domain,
      live: source.live,
      visited: source.visited,
      sources: [],
    };
    group.sources.push(source);
    groups.set(key, group);
  }
  return [...groups.values()];
}

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
  const pages = groupByPage(r?.sources ?? []);

  // Jump to where a citation comes from: switch to (or open) its page and
  // highlight the supporting sentences.
  async function show(source: SourceOut) {
    const result = await showPassages(source.url, [passageOf(source)], {
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
            {message.note && <div className="note">{message.note}</div>}
            <AnswerText
              text={message.content}
              citable={new Set(sources.keys())}
              onCite={(n) => void show(sources.get(n)!)}
            />
            {pages.length > 0 && (
              <div className="sources">
                {pages.map((page) => (
                  <div className="source-page" key={page.key}>
                    <a href={page.url} target="_blank" rel="noreferrer">
                      {page.title}
                    </a>
                    <span className="meta">
                      {page.domain} · {page.live ? "open tab" : page.visited.slice(0, 10)}
                    </span>
                    {page.sources.map((s) => (
                      <button
                        key={s.n}
                        className="passage"
                        title="Show this on the page"
                        onClick={() => void show(s)}
                      >
                        <b>[{s.n}]</b>{" "}
                        {s.heading_path.length > 0
                          ? s.heading_path.slice(-2).join(" › ")
                          : "show on page"}
                        {missing.includes(s.n) && (
                          <span className="lost"> · not found on the page (it may have changed)</span>
                        )}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
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
