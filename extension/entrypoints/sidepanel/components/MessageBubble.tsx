import { ChatMessage } from "../hooks/useChat";

// Content renders as PLAIN TEXT, deliberately. The answer contains retrieved
// page text; innerHTML-style rendering would let a malicious page inject
// markup that executes inside the extension.
export default function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="row user">
        <div className="bubble user">{message.content}</div>
      </div>
    );
  }

  const r = message.result;
  const abstained = r?.abstained;

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
            <p>{message.content}</p>
            {r && r.sources.length > 0 && (
              <ol className="sources">
                {r.sources.map((s) => (
                  <li key={s.n} value={s.n}>
                    <a href={s.url} target="_blank" rel="noreferrer">
                      {s.title}
                    </a>
                    <span className="meta">
                      {s.domain} · {s.visited.slice(0, 10)}
                    </span>
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
