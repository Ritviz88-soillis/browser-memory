import { useState } from "react";
import { SourceOut } from "@/utils/api";
import { openPdfAt, pageKey, passageOf, pdfPageOf, showPassages } from "@/utils/passages";
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

    // a PDF cannot be highlighted, but it can be opened at the cited page
    const pdfPage = result === null ? pdfPageOf(source) : null;
    if (pdfPage !== null) {
      await openPdfAt(source.url, pdfPage, source.tab_id).catch(() => null);
      setMissing((m) => m.filter((n) => n !== source.n));
      return;
    }

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
            {r?.trust && <TrustLine trust={r.trust} />}
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

// One line under each answer: how many of its cited statements were found on
// the page they cite. The check compares words, so it is a strong signal,
// not a proof; the tooltip says so.
function TrustLine({ trust }: { trust: { verified: number; unverified: number } }) {
  const checked = trust.verified + trust.unverified;
  if (checked === 0) return null;
  const allFound = trust.unverified === 0;
  return (
    <div
      className={`trust ${allFound ? "ok" : "warn"}`}
      title="Each cited statement is compared with the passage it cites. A statement counts as found when most of its key words are in that passage. This catches statements about things the page never mentions; it cannot prove a statement is true."
    >
      {allFound ? "✓" : "!"} {trust.verified} of {checked} cited{" "}
      {checked === 1 ? "statement" : "statements"} found on the page
      {allFound ? "" : ` · ${trust.unverified} not found`}
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
