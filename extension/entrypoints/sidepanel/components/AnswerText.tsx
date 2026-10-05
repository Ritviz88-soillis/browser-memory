// Renders an answer: paragraphs, bulleted and numbered lists, code blocks,
// `inline code`, **bold**, and [n] citations as buttons.
//
// The answer contains text taken from web pages, so it is NEVER rendered as
// HTML. The string is split by hand and every piece becomes a React text node
// inside an element created here; nothing a page wrote can turn into markup.

import { ReactNode } from "react";

interface Props {
  text: string;
  citable: Set<number>; // source numbers that can be shown on a page
  onCite: (n: number) => void;
}

type Block =
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "paragraph"; text: string };

const FENCE_RE = /```[\w-]*\n?([\s\S]*?)```/g;
const LIST_ITEM_RE = /^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/;
// "[?]" is what the server puts in place of a citation when the sentence was
// not found on the page it cited.
const INLINE_RE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\d{1,3}\]|\[\?\])/;

function proseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    if (list) blocks.push({ kind: "list", ...list });
    paragraph = [];
    list = null;
  };

  for (const raw of text.split("\n")) {
    const line = raw.replace(/^#{1,6}\s+/, ""); // a heading reads fine as a line
    const item = LIST_ITEM_RE.exec(line);
    if (item) {
      if (paragraph.length) flush();
      list ??= { ordered: item[1] !== undefined, items: [] };
      list.items.push(item[2]);
    } else if (!line.trim()) {
      flush();
    } else {
      if (list) flush();
      paragraph.push(line.trim());
    }
  }
  flush();
  return blocks;
}

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let last = 0;
  for (const match of text.matchAll(FENCE_RE)) {
    blocks.push(...proseBlocks(text.slice(last, match.index)));
    blocks.push({ kind: "code", text: match[1].replace(/\n$/, "") });
    last = match.index! + match[0].length;
  }
  blocks.push(...proseBlocks(text.slice(last)));
  return blocks;
}

export default function AnswerText({ text, citable, onCite }: Props) {
  function inline(source: string): ReactNode[] {
    return source.split(INLINE_RE).map((part, i) => {
      if (/^`[^`\n]+`$/.test(part)) return <code key={i}>{part.slice(1, -1)}</code>;
      if (/^\*\*[^*\n]+\*\*$/.test(part)) return <strong key={i}>{part.slice(2, -2)}</strong>;
      if (part === "[?]") {
        return (
          <span
            key={i}
            className="unverified"
            title="This sentence was not found on the page it cited. Treat it as the assistant's own addition, not something you read."
          >
            not on the page
          </span>
        );
      }
      if (/^\[\d{1,3}\]$/.test(part)) {
        const n = Number(part.slice(1, -1));
        if (citable.has(n)) {
          return (
            <button
              key={i}
              className="cite"
              title="Show where this comes from on the page"
              onClick={() => onCite(n)}
            >
              {part}
            </button>
          );
        }
      }
      return part;
    });
  }

  return (
    <div className="answer">
      {toBlocks(text).map((block, i) => {
        if (block.kind === "code") return <pre key={i}>{block.text}</pre>;
        if (block.kind === "list") {
          const items = block.items.map((item, j) => <li key={j}>{inline(item)}</li>);
          return block.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
        }
        return <p key={i}>{inline(block.text)}</p>;
      })}
    </div>
  );
}
