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
import TabPicker from "./TabPicker";

type Panel = "chat" | "history" | "tabs";

export default function ChatView() {
  const { messages, busy, send, newChat, loadConversation } = useChat();
  const [draft, setDraft] = useState("");
  const [panel, setPanel] = useState<Panel>("chat");
  const [past, setPast] = useState<Conversation[]>([]);
  // tabs ticked to summarise or compare; empty = normal memory + current page
  const [selectedTabs, setSelectedTabs] = useState<number[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => {
    if (panel === "history") void listConversations().then(setPast);
  }, [panel]);

  function submit() {
    if (!draft.trim() || busy) return;
    void send(draft, selectedTabs);
    setDraft("");
    setPanel("chat");
  }

  function removeConversation(id: string) {
    void deleteConversation(id).then(listConversations).then(setPast);
  }

  const comparing = selectedTabs.length > 0;

  return (
    <div className="chat">
      <ContextChip />
      <RelatedPages />
      <div className="chat-toolbar">
        <button
          className={panel === "tabs" ? "on" : ""}
          onClick={() => setPanel(panel === "tabs" ? "chat" : "tabs")}
        >
          {panel === "tabs" ? "done" : "compare tabs"}
        </button>
        <button onClick={() => setPanel(panel === "history" ? "chat" : "history")}>
          {panel === "history" ? "back to chat" : "history"}
        </button>
        <button
          onClick={() => {
            newChat();
            setPanel("chat");
          }}
        >
          + new chat
        </button>
      </div>

      {panel === "history" && (
        <ul className="conv-list">
          {past.length === 0 && <li className="none">No past conversations yet.</li>}
          {past.map((c) => (
            <li key={c.id}>
              <button
                className="conv"
                onClick={() => {
                  loadConversation(c);
                  setPanel("chat");
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
      )}

      {panel === "tabs" && (
        <TabPicker selected={selectedTabs} onChange={setSelectedTabs} />
      )}

      {panel !== "history" && (
        <>
          {panel === "chat" && (
            <div className="history" ref={scrollRef}>
              {messages.length === 0 ? (
                <div className="empty">
                  <h2>Browser Memory</h2>
                  <p>
                    Ask about anything you've read — "what did I read about X
                    last week?" — or about the page you're on right now.
                  </p>
                  <p className="faint">
                    Use "compare tabs" to summarise or compare several open
                    tabs at once.
                  </p>
                </div>
              ) : (
                <>
                  {messages.map((m) => (
                    <MessageBubble key={m.id} message={m} />
                  ))}
                </>
              )}
            </div>
          )}
          {comparing && (
            <div className="comparing">
              <span>
                Answering from {selectedTabs.length} selected{" "}
                {selectedTabs.length === 1 ? "tab" : "tabs"}
              </span>
              <button onClick={() => setSelectedTabs([])}>clear</button>
            </div>
          )}
          <div className="composer">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder={
                comparing
                  ? "Summarise or compare the selected tabs…"
                  : "Ask about this page or your history…"
              }
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
