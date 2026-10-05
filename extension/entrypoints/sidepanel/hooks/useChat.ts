import { useEffect, useState } from "react";
import { api, AskOut, CurrentPage } from "@/utils/api";
import { getCurrentPageContext, getPageContext } from "@/utils/pageContext";
import { showPassages } from "@/utils/passages";
import { Conversation, saveConversation } from "@/utils/chatStore";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: AskOut; // sources + filters, assistant turns only
  note?: string; // e.g. a ticked tab that couldn't be read
  error?: boolean;
  pending?: boolean;
}

const HISTORY_TURNS = 6;

async function readTab(tabId: number): Promise<CurrentPage | null> {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return tab ? getPageContext(tab, { withHtml: true }) : null;
}

// A video page has no article text, but the server can fetch its transcript.
function isReadable(page: CurrentPage): boolean {
  return Boolean(page.html || page.text) || /youtube\.com|youtu\.be/.test(page.url);
}

// As soon as an answer arrives, light up the passages it cites on the pages
// that are already open — without stealing focus from the tab being read.
async function highlightCitedOpenPassages(result: AskOut): Promise<void> {
  const byPage = new Map<string, { url: string; tabId: number | null; passages: string[] }>();
  for (const source of result.sources) {
    if (!source.live) continue;
    const key = `${source.tab_id}|${source.url}`;
    const entry = byPage.get(key) ?? { url: source.url, tabId: source.tab_id, passages: [] };
    entry.passages.push(source.passage);
    byPage.set(key, entry);
  }
  for (const { url, tabId, passages } of byPage.values()) {
    await showPassages(url, passages, { tabId, bringToFront: false }).catch(() => null);
  }
}

export function useChat() {
  // fresh id per panel load = every open starts a new conversation
  const [conversationId, setConversationId] = useState<string>(() =>
    crypto.randomUUID(),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);

  // persist after each completed exchange; errors/in-flight stay session-only
  useEffect(() => {
    const storable = messages.filter((m) => !m.pending && !m.error);
    if (storable.length < 2) return; // nothing worth keeping before Q + A
    void saveConversation({
      id: conversationId,
      title: (storable.find((m) => m.role === "user")?.content ?? "chat").slice(0, 60),
      updatedAt: Date.now(),
      messages: storable.map(({ id, role, content, result }) => ({
        id,
        role,
        content,
        result,
      })),
    });
  }, [messages, conversationId]);

  // tabIds: tabs ticked in the picker. When given, the answer comes from
  // those tabs only; otherwise from memory plus the page open right now.
  async function send(question: string, tabIds: number[] = []) {
    if (busy || !question.trim()) return;
    setBusy(true);

    // history sent BEFORE appending the new question, capped server-side too
    const history = messages
      .filter((m) => !m.error && !m.pending)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.content }));

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: question.trim(),
    };
    const pendingId = crypto.randomUUID();
    setMessages((m) => [
      ...m,
      userMsg,
      { id: pendingId, role: "assistant", content: "", pending: true },
    ]);

    try {
      let currentPage: CurrentPage | null = null;
      let tabs: CurrentPage[] = [];
      let note: string | undefined;

      if (tabIds.length) {
        const picked = await Promise.all(tabIds.map(readTab));
        tabs = picked.filter((p): p is CurrentPage => p !== null && isReadable(p));
        const unread = tabIds.length - tabs.length;
        if (!tabs.length) throw new Error("None of the selected tabs could be read.");
        if (unread) {
          note = `${unread} selected ${unread === 1 ? "tab" : "tabs"} couldn't be read (a sleeping tab needs to be opened once).`;
        }
      } else {
        currentPage = await getCurrentPageContext({ withHtml: true });
      }

      const result = await api.ask(question.trim(), history, currentPage, tabs);
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, content: result.answer, result, note, pending: false }
            : msg,
        ),
      );
      void highlightCitedOpenPassages(result);
    } catch (e) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? {
                ...msg,
                content: e instanceof Error ? e.message : String(e),
                error: true,
                pending: false,
              }
            : msg,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  function newChat() {
    setConversationId(crypto.randomUUID());
    setMessages([]);
  }

  function loadConversation(conv: Conversation) {
    setConversationId(conv.id);
    setMessages(conv.messages);
  }

  return { messages, busy, send, newChat, loadConversation };
}
