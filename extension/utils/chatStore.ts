// Past conversations in chrome.storage.local, newest first. The side panel
// always OPENS on a fresh chat; history exists for revisiting old ones.
// Errors and in-flight turns are never stored — only completed exchanges.

import type { AskOut } from "./api";

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: AskOut;
}

export interface Conversation {
  id: string;
  title: string; // first question, truncated
  updatedAt: number;
  messages: StoredMessage[];
}

const KEY = "conversations";
const MAX_CONVERSATIONS = 50;

export async function listConversations(): Promise<Conversation[]> {
  const stored = await chrome.storage.local.get(KEY);
  return stored[KEY] ?? [];
}

export async function saveConversation(conv: Conversation): Promise<void> {
  const rest = (await listConversations()).filter((c) => c.id !== conv.id);
  await chrome.storage.local.set({
    [KEY]: [conv, ...rest].slice(0, MAX_CONVERSATIONS),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  const all = await listConversations();
  await chrome.storage.local.set({ [KEY]: all.filter((c) => c.id !== id) });
}
