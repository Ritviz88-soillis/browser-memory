import { useEffect, useState } from "react";
import { api, AskOut } from "@/utils/api";
import { getCurrentPageContext } from "@/utils/pageContext";
import { Conversation, saveConversation } from "@/utils/chatStore";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: AskOut; // sources + filters, assistant turns only
  error?: boolean;
  pending?: boolean;
}

const HISTORY_TURNS = 6;

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

  async function send(question: string) {
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
      const currentPage = await getCurrentPageContext();
      const result = await api.ask(question.trim(), history, currentPage);
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, content: result.answer, result, pending: false }
            : msg,
        ),
      );
    } catch (e) {
      setMessages((m) =>
        m.map((msg) =>
          msg.id === pendingId
            ? { ...msg, content: String(e), error: true, pending: false }
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
