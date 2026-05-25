export type ChatRole = 'user' | 'llm';

export interface ChatLine {
  id: string;
  role: ChatRole;
  text: string;
}

/** Upsert the in-flight assistant bubble; never remove it here. */
export function upsertActiveLlmMessage(
  messages: ChatLine[],
  activeId: string | null,
  text: string,
): { messages: ChatLine[]; activeId: string } {
  const trimmed = text.trim();
  if (!trimmed) {
    return { messages, activeId: activeId ?? '' };
  }

  const id = activeId ?? `llm-${Date.now()}`;
  const line: ChatLine = { id, role: 'llm', text: trimmed };
  const idx = messages.findIndex((m) => m.id === id);
  if (idx >= 0) {
    return {
      messages: [...messages.slice(0, idx), line, ...messages.slice(idx + 1)],
      activeId: id,
    };
  }
  return { messages: [...messages, line], activeId: id };
}

/** Append a user line; if the active LLM bubble arrived early, place user before it. */
export function appendUserMessage(
  messages: ChatLine[],
  user: ChatLine,
  activeLlmId: string | null,
): ChatLine[] {
  const last = messages.at(-1);
  if (last?.role === 'llm' && activeLlmId && last.id === activeLlmId) {
    return [...messages.slice(0, -1), user, last];
  }
  return [...messages, user];
}

export function lastCommittedMessageIsUser(messages: ChatLine[]): boolean {
  return messages.at(-1)?.role === 'user';
}

/** Drop the in-flight assistant bubble on barge-in / interrupt. */
export function removeActiveLlmMessage(
  messages: ChatLine[],
  activeId: string | null,
): ChatLine[] {
  if (!activeId) return messages;
  return messages.filter((m) => m.id !== activeId);
}
