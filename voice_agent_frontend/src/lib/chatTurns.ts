import { formatChatDisplayText, formatUserChatDisplayText } from './chatDisplay';

export interface ChatTurn {
  id: string;
  /** Backend turn_seq; 0 until the first final for this utterance. */
  userTurnSeq: number;
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

/** Merge streaming STT segments (Deepgram sends incremental finals, not always full text). */
export function mergeUserSpeechText(previous: string, incoming: string): string {
  const inc = incoming.trim();
  if (!inc) return previous.trim();
  const prev = previous.trim();
  if (!prev) return inc;
  if (inc === prev || inc.startsWith(prev)) return inc;
  if (prev.startsWith(inc)) return prev;
  if (prev.endsWith(inc)) return prev;
  return `${prev} ${inc}`.trim();
}

export function createTurn(turnId: string, userTurnSeq = 0): ChatTurn {
  return {
    id: turnId,
    userTurnSeq,
    userText: '',
    userFinal: false,
    assistantText: '',
    assistantStreaming: false,
  };
}

function resolveUserTurnIndex(turns: ChatTurn[], turnSeq?: number): number {
  if (turnSeq != null && turnSeq > 0) {
    const bySeq = turns.findIndex((t) => t.userTurnSeq === turnSeq);
    if (bySeq >= 0) return bySeq;
    const maxSeq = Math.max(0, ...turns.map((t) => t.userTurnSeq));
    if (turnSeq > maxSeq) {
      const open = turns.at(-1);
      if (open && !open.userFinal) return turns.length - 1;
      return -1;
    }
  }

  const last = turns.at(-1);
  if (!last) return -1;
  if (!last.userFinal) return turns.length - 1;

  const assistantStarted =
    last.assistantStreaming || last.assistantText.trim().length > 0;
  if (!assistantStarted) return turns.length - 1;

  return -1;
}

/** Interim or final user speech — targets the open turn for this utterance (turn_seq). */
export function applyUserTranscript(
  turns: ChatTurn[],
  text: string,
  isFinal: boolean,
  turnSeq?: number,
): ChatTurn[] {
  const trimmed = text.trim();
  if (!trimmed) return turns;

  const next = [...turns];
  let idx = resolveUserTurnIndex(next, turnSeq);

  if (idx < 0) {
    const seq = turnSeq && turnSeq > 0 ? turnSeq : 0;
    const id = seq > 0 ? `turn-${seq}` : `turn-${Date.now()}`;
    next.push(createTurn(id, seq));
    idx = next.length - 1;
  }

  const turn = next[idx]!;
  const effectiveSeq =
    turnSeq && turnSeq > 0 ? turnSeq : turn.userTurnSeq;

  next[idx] = {
    ...turn,
    userTurnSeq: effectiveSeq > 0 ? effectiveSeq : turn.userTurnSeq,
    userText: isFinal
      ? trimmed
      : mergeUserSpeechText(turn.userText, trimmed),
    userFinal: isFinal || turn.userFinal,
  };
  return next;
}

/** LLM text attaches to the latest turn with a finalized user message. */
export function applyAssistantTranscript(
  turns: ChatTurn[],
  text: string,
  streaming: boolean,
): ChatTurn[] {
  const trimmed = text.trim();
  if (!trimmed) return turns;

  const next = [...turns];
  const last = next.at(-1);

  if (!last?.userFinal) {
    if (turns.length === 0) {
      next.push({
        ...createTurn('turn-greeting', 0),
        userFinal: true,
        assistantText: trimmed,
        assistantStreaming: streaming,
      });
      return next;
    }
    return turns;
  }

  const idx = next.length - 1;
  if (
    trimmed === last.assistantText.trim() &&
    (!streaming || last.assistantStreaming)
  ) {
    if (!streaming && last.assistantStreaming) {
      next[idx] = { ...last, assistantStreaming: false };
    }
    return next;
  }

  next[idx] = {
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
    const userDisplay = formatUserChatDisplayText(turn.userText);
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
