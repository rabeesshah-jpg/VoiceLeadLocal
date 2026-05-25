import { formatChatDisplayText } from './chatDisplay';

export interface ChatTurn {
  id: string;
  userText: string;
  userFinal: boolean;
  assistantText: string;
  assistantStreaming: boolean;
}

export interface ChatBubble {
  id: string;
  role: 'user' | 'llm';
  text: string;
  pending: boolean;
}

export function createTurn(turnId: string): ChatTurn {
  return {
    id: turnId,
    userText: '',
    userFinal: false,
    assistantText: '',
    assistantStreaming: false,
  };
}

/** Interim or final user speech — always targets the open (last) turn. */
export function applyUserTranscript(
  turns: ChatTurn[],
  text: string,
  isFinal: boolean,
): ChatTurn[] {
  const trimmed = text.trim();
  if (!trimmed) return turns;

  const next = [...turns];
  const last = next.at(-1);

  const needsNewTurn =
    !last ||
    (last.userFinal &&
      (last.assistantStreaming ||
        (last.assistantText.trim().length > 0 && !last.assistantStreaming)));

  if (needsNewTurn) {
    next.push(createTurn(`turn-${Date.now()}`));
  }

  const idx = next.length - 1;
  next[idx] = {
    ...next[idx]!,
    userText: trimmed,
    userFinal: isFinal || next[idx]!.userFinal,
  };
  return next;
}

/** LLM text only attaches to the latest turn that has a finalized user message. */
export function applyAssistantTranscript(
  turns: ChatTurn[],
  text: string,
  streaming: boolean,
): ChatTurn[] {
  const trimmed = text.trim();
  if (!trimmed) return turns;

  const next = [...turns];
  const last = next.at(-1);
  if (!last?.userFinal) return turns;

  next[next.length - 1] = {
    ...last,
    assistantText: trimmed,
    assistantStreaming: streaming,
  };
  return next;
}

export function finishAssistantStreaming(turns: ChatTurn[]): ChatTurn[] {
  return turns.map((t) =>
    t.assistantStreaming ? { ...t, assistantStreaming: false } : t,
  );
}

export function removeIncompleteAssistant(turns: ChatTurn[]): ChatTurn[] {
  const next = [...turns];
  const last = next.at(-1);
  if (last?.assistantStreaming && !last.assistantText.trim()) {
    next.pop();
  } else if (last?.assistantStreaming) {
    next[next.length - 1] = { ...last, assistantText: '', assistantStreaming: false };
  }
  return next;
}

/** Flatten turns into alternating user → assistant bubbles for the UI. */
export function turnsToBubbles(turns: ChatTurn[]): ChatBubble[] {
  const out: ChatBubble[] = [];
  for (const turn of turns) {
    const userDisplay = formatChatDisplayText(turn.userText);
    if (userDisplay) {
      out.push({
        id: `${turn.id}-user`,
        role: 'user',
        text: userDisplay,
        pending: !turn.userFinal,
      });
    }
    const assistantDisplay = formatChatDisplayText(turn.assistantText);
    if (assistantDisplay) {
      out.push({
        id: `${turn.id}-assistant`,
        role: 'llm',
        text: assistantDisplay,
        pending: turn.assistantStreaming,
      });
    }
  }
  return out;
}
