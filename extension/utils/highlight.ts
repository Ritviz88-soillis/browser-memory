// Find cited passages in the live page and highlight them. Runs inside the
// page (content script).
//
// The passage text comes from Readability's cleaned copy of the page, so its
// whitespace differs from the live DOM (block boundaries, indentation). Both
// sides are therefore compared with ALL whitespace removed, through an index
// that maps each remaining character back to its text node.
//
// Highlighting uses the CSS Custom Highlight API: ranges are painted without
// touching the page's DOM, so the original page is left exactly as it was.

const HIGHLIGHT_NAME = "browser-memory";
const PASSAGE_HIGHLIGHT = "browser-memory-passage";
const STYLE_ID = "browser-memory-highlight-style";
const SKIPPED_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TEXTAREA"]);
const MIN_BLOCK_CHARS = 12;
const ANCHOR_CHARS = 60;

interface TextIndex {
  text: string; // page text, lowercased, whitespace removed
  nodes: Text[];
  nodeOf: Int32Array; // text[i] lives in nodes[nodeOf[i]]…
  offsetOf: Int32Array; // …at this offset
}

function isWhitespace(code: number): boolean {
  return (
    (code >= 9 && code <= 13) ||
    code === 32 ||
    code === 160 ||
    code === 0x1680 ||
    (code >= 0x2000 && code <= 0x200a) ||
    code === 0x2028 ||
    code === 0x2029 ||
    code === 0x202f ||
    code === 0x205f ||
    code === 0x3000 ||
    code === 0xfeff
  );
}

function squash(text: string): string {
  let out = "";
  for (let i = 0; i < text.length; i++) {
    if (!isWhitespace(text.charCodeAt(i))) out += text[i];
  }
  return out.toLowerCase();
}

function buildIndex(): TextIndex {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) =>
      SKIPPED_TAGS.has(node.parentElement?.tagName ?? "")
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT,
  });

  const nodes: Text[] = [];
  const parts: string[] = [];
  const nodeOf: number[] = [];
  const offsetOf: number[] = [];

  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const data = (node as Text).data;
    const lowered = data.toLowerCase();
    // toLowerCase can change length for a few characters; keep offsets exact
    const source = lowered.length === data.length ? lowered : data;
    const nodeNumber = nodes.push(node as Text) - 1;
    let kept = "";
    for (let i = 0; i < source.length; i++) {
      if (isWhitespace(source.charCodeAt(i))) continue;
      kept += source[i];
      nodeOf.push(nodeNumber);
      offsetOf.push(i);
    }
    parts.push(kept);
  }

  return {
    text: parts.join(""),
    nodes,
    nodeOf: Int32Array.from(nodeOf),
    offsetOf: Int32Array.from(offsetOf),
  };
}

function rangeBetween(index: TextIndex, start: number, end: number): Range {
  // [start, end) in squashed coordinates
  const range = document.createRange();
  range.setStart(index.nodes[index.nodeOf[start]], index.offsetOf[start]);
  range.setEnd(index.nodes[index.nodeOf[end - 1]], index.offsetOf[end - 1] + 1);
  return range;
}

function locateBlock(index: TextIndex, block: string, from: number): [number, number] | null {
  let at = index.text.indexOf(block, from);
  if (at < 0) at = index.text.indexOf(block);
  if (at >= 0) return [at, at + block.length];

  // The page differs slightly inside the block (a footnote marker, a
  // redaction): anchor on its first and last characters instead.
  if (block.length < ANCHOR_CHARS * 2) return null;
  const head = index.text.indexOf(block.slice(0, ANCHOR_CHARS));
  if (head < 0) return null;
  const tail = index.text.indexOf(block.slice(-ANCHOR_CHARS), head);
  if (tail < 0 || tail - head > block.length * 1.5) return null;
  return [head, tail + ANCHOR_CHARS];
}

// Where a passage sits on the page, as [start, end) spans in squashed text.
function locatePassage(index: TextIndex, passage: string): [number, number][] {
  const whole = squash(passage);
  if (!whole) return [];

  const at = index.text.indexOf(whole);
  if (at >= 0) return [[at, at + whole.length]];

  // A passage is several blocks (heading, paragraphs) joined by blank lines;
  // locate each on its own.
  const spans: [number, number][] = [];
  let cursor = 0;
  for (const block of passage.split(/\n\s*\n/)) {
    const squashed = squash(block);
    if (squashed.length < MIN_BLOCK_CHARS) continue;
    const found = locateBlock(index, squashed, cursor);
    if (!found) continue;
    spans.push(found);
    cursor = found[1];
  }
  return spans;
}

function ensureStyle(): void {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  // two levels: the cited passage as a faint wash, and inside it the
  // sentences that support the answer in solid yellow
  style.textContent =
    `::highlight(${PASSAGE_HIGHLIGHT}) { background-color: rgba(255, 214, 51, 0.22); }\n` +
    `::highlight(${HIGHLIGHT_NAME}) { background-color: #ffd633; color: #111; }`;
  document.documentElement.appendChild(style);
}

export function clearHighlights(): void {
  (CSS as any).highlights?.delete(HIGHLIGHT_NAME);
  (CSS as any).highlights?.delete(PASSAGE_HIGHLIGHT);
}

export interface PassageSpec {
  text: string; // the cited passage
  key?: string[]; // the sentences in it that support the answer
}

// Marks each cited passage faintly and its supporting sentences strongly,
// then scrolls to the first supporting sentence. A passage with no supporting
// sentences singled out is marked strongly as a whole.
// Returns how many of the passages were located on this page.
export function highlightPassages(passages: PassageSpec[]): { found: number; total: number } {
  clearHighlights();
  const total = passages.length;
  if (!document.body) return { found: 0, total };

  const index = buildIndex();
  const passageRanges: Range[] = [];
  const keyRanges: Range[] = [];
  let found = 0;

  for (const passage of passages) {
    const located = locatePassage(index, passage.text);
    const from = located.length ? located[0][0] : 0;

    const keys: Range[] = [];
    for (const sentence of passage.key ?? []) {
      const squashed = squash(sentence);
      if (squashed.length < MIN_BLOCK_CHARS) continue;
      // look inside the passage first, so a repeated sentence elsewhere on
      // the page is not picked instead
      const span = locateBlock(index, squashed, from);
      if (span) keys.push(rangeBetween(index, span[0], span[1]));
    }

    if (located.length || keys.length) found += 1;
    const whole = located.map(([start, end]) => rangeBetween(index, start, end));
    if (keys.length) {
      passageRanges.push(...whole);
      keyRanges.push(...keys);
    } else {
      keyRanges.push(...whole);
    }
  }

  if (!keyRanges.length) return { found: 0, total };

  const registry = (CSS as any).highlights;
  const Highlight = (window as any).Highlight;
  if (registry && typeof Highlight === "function") {
    ensureStyle();
    if (passageRanges.length) registry.set(PASSAGE_HIGHLIGHT, new Highlight(...passageRanges));
    const strong = new Highlight(...keyRanges);
    strong.priority = 1; // drawn over the faint passage wash
    registry.set(HIGHLIGHT_NAME, strong);
  } else {
    // very old browser: fall back to selecting the first sentence
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(keyRanges[0]);
  }

  // the first supporting sentence in page order, not in citation order
  const first = keyRanges.reduce((a, b) =>
    a.compareBoundaryPoints(Range.START_TO_START, b) <= 0 ? a : b,
  );
  first.startContainer.parentElement?.scrollIntoView({ behavior: "smooth", block: "center" });
  return { found, total };
}
