import { useEffect, useRef, useState } from "react";
import {
  Conversation,
  deleteConversation,
  listConversations,
} from "@/utils/chatStore";
import { useChat } from "../hooks/useChat";
import ContextChip from "./ContextChip";
import MessageBubble from "./MessageBubble";
import RelatedPages from "./RelatedPages";

export default function ChatView() {
  const { messages, busy, send, newChat, loadConversation } = useChat();
  const [draft, setDraft] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [past, setPast] = useState<Conversation[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => {
    if (showHistory) void listConversations().then(setPast);
  }, [showHistory]);

  function submit() {
    if (!draft.trim() || busy) return;
    void send(draft);
    setDraft("");
  }

  function removeConversation(id: string) {
    void deleteConversation(id).then(listConversations).then(setPast);
  }

  return (
    <div className="chat">
      <ContextChip />
      <RelatedPages />
      <div className="chat-toolbar">
        <button onClick={() => setShowHistory(!showHistory)}>
          {showHistory ? "back to chat" : "history"}
        </button>
        <button
          onClick={() => {
            newChat();
            setShowHistory(false);
          }}
        >
          + new chat
        </button>
      </div>

      {showHistory ? (
        <ul className="conv-list">
          {past.length === 0 && <li className="none">No past conversations yet.</li>}
          {past.map((c) => (
            <li key={c.id}>
              <button
                className="conv"
                onClick={() => {
                  loadConversation(c);
                  setShowHistory(false);
                }}
              >
                <span className="conv-title">{c.title}</span>
                <span className="meta">
                  {new Date(c.updatedAt).toLocaleString()} ·{" "}
                  {c.messages.length} messages
                </span>
              </button>
              <button
                className="del"
                title="delete conversation"
                onClick={() => removeConversation(c.id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <>
          <div className="history" ref={scrollRef}>
            {messages.length === 0 ? (
              <div className="empty">
                <h2>Browser Memory</h2>
                <p>
                  Ask about anything you've read — "what did I read about X last
                  week?" — or discuss the page you're on right now.
                </p>
                <p className="faint">Pages index automatically as you browse.</p>
              </div>
            ) : (
              <>
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
                ))}
              </>
            )}
          </div>
          <div className="composer">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder="Ask about this page or your history…"
              disabled={busy}
            />
            <button onClick={submit} disabled={busy || !draft.trim()}>
              Ask
            </button>
          </div>
        </>
      )}
    </div>
  );
}
