import {
  formatChatDisplayText,
  formatUserChatDisplayText,
  sanitizeUserSpokenText,
} from './chatDisplay';

export interface ChatTurn {
  id: string;
  userMessageId: string;
  assistantMessageId: string;
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

/** Tracks the active user utterance — cleared after each final. */
export interface UserTranscriptSession {
  activeUserMessageId: string | null;
  activeTurnSeq: number;
  partialBuffer: string;
  draftOpen: boolean;
  lastFinalizedUserText: string;
  lastFinalizedTurnSeq: number;
  lastPartialNormalized: string;
  lastAssistantNormalized: string;
}

export function createUserTranscriptSession(): UserTranscriptSession {
  return {
    activeUserMessageId: null,
    activeTurnSeq: 0,
    partialBuffer: '',
    draftOpen: false,
    lastFinalizedUserText: '',
    lastFinalizedTurnSeq: 0,
    lastPartialNormalized: '',
    lastAssistantNormalized: '',
  };
}

export function clearActiveUserTurn(session: UserTranscriptSession): UserTranscriptSession {
  return {
    ...session,
    activeUserMessageId: null,
    activeTurnSeq: 0,
    partialBuffer: '',
    draftOpen: false,
    lastPartialNormalized: '',
  };
}

const FINAL_DEDUPE_WINDOW_MS = 2500;

/** Normalize text for final dedupe keys. */
export function normalizeTranscriptDedupeKey(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ');
}

function normalizeForStrip(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').trim();
}

/**
 * Faster Whisper / WLK often sends session-cumulative text.
 * For a new user turn, keep only the portion after the last finalized user line.
 */
export function stripPriorUserTextFromCumulative(
  incoming: string,
  priorFinalUserText: string,
): string {
  const inc = incoming.trim();
  const prior = priorFinalUserText.trim();
  if (!inc) return '';
  if (!prior) return inc;
  if (inc === prior) return '';
  if (inc.startsWith(prior)) {
    const rest = inc.slice(prior.length).replace(/^[\s,.:;!?-]+/, '').trim();
    return rest;
  }
  const idx = inc.lastIndexOf(prior);
  if (idx >= 0) {
    const rest = inc.slice(idx + prior.length).replace(/^[\s,.:;!?-]+/, '').trim();
    if (rest) return rest;
  }

  const nInc = normalizeForStrip(inc);
  const nPrior = normalizeForStrip(prior);
  if (nPrior && nInc.startsWith(nPrior)) {
    const priorWords = prior.split(/\s+/).filter(Boolean);
    const incWords = inc.split(/\s+/).filter(Boolean);
    if (incWords.length > priorWords.length) {
      return incWords.slice(priorWords.length).join(' ').trim();
    }
    if (nInc === nPrior) return '';
  }

  return inc;
}

/** All finalized user lines in the conversation (longest first for stripping). */
export function getCommittedUserTexts(
  turns: ChatTurn[],
  excludeTurnSeq?: number,
): string[] {
  return turns
    .filter(
      (t) =>
        t.userFinal &&
        t.userText.trim() &&
        (excludeTurnSeq == null || excludeTurnSeq <= 0 || t.userTurnSeq !== excludeTurnSeq),
    )
    .map((t) => t.userText.trim())
    .sort((a, b) => b.length - a.length);
}

/** Remove every prior finalized user line from cumulative STT payloads. */
export function stripAllCommittedUserTexts(
  incoming: string,
  committed: string[],
): string {
  let result = incoming.trim();
  if (!result || committed.length === 0) return result;

  const joined = committed.join(' ').trim();
  if (joined) {
    result = stripPriorUserTextFromCumulative(result, joined);
  }

  let changed = true;
  while (changed) {
    changed = false;
    for (const prior of committed) {
      const next = stripPriorUserTextFromCumulative(result, prior);
      if (next !== result) {
        result = next;
        changed = true;
      }
    }
  }
  return result.trim();
}

/** When prefix strip fails, take word tail after all committed words. */
export function fallbackUtteranceAfterStrip(
  incoming: string,
  committed: string[],
): string {
  const inc = incoming.trim();
  if (!inc) return '';
  if (committed.length === 0) return inc;

  const joined = committed.join(' ').trim();
  const jWords = joined.split(/\s+/).filter(Boolean);
  const iWords = inc.split(/\s+/).filter(Boolean);
  if (jWords.length > 0 && iWords.length > jWords.length) {
    const tail = iWords.slice(jWords.length).join(' ').trim();
    if (tail.length >= 1) return tail;
  }

  const nInc = normalizeForStrip(inc);
  const nJoined = normalizeForStrip(joined);
  if (nJoined && nInc.includes(nJoined)) {
    return '';
  }
  return inc;
}

/** True when STT payload still embeds multiple prior user lines (do not render). */
export function isLeakyCumulativeUserText(
  text: string,
  turns: ChatTurn[],
): boolean {
  const committed = getCommittedUserTexts(turns);
  if (committed.length === 0) return false;

  const nText = normalizeTranscriptDedupeKey(text);
  if (!nText) return false;

  let embedded = 0;
  for (const c of committed) {
    const nC = normalizeTranscriptDedupeKey(c);
    if (nC.length >= 6 && nText.includes(nC)) embedded += 1;
  }
  if (embedded >= 2) return true;

  const longest = committed[0] ?? '';
  const nLong = normalizeTranscriptDedupeKey(longest);
  return (
    nLong.length >= 10 &&
    nText.includes(nLong) &&
    text.length > longest.length * 1.1
  );
}

/** Remove in-progress user draft (e.g. junk cumulative STT while agent speaks). */
export function discardOpenUserDraft(turns: ChatTurn[]): ChatTurn[] {
  return turns.filter((t) => t.userFinal);
}

/**
 * Current-utterance text only: strip prior turns, then merge within draft.
 */
export function localizeUserSttInput(
  incoming: string,
  turns: ChatTurn[],
  session: UserTranscriptSession,
  excludeTurnSeq?: number,
): string {
  const committed = getCommittedUserTexts(turns, excludeTurnSeq);
  let text = stripAllCommittedUserTexts(incoming, committed);
  if (!text) {
    text = fallbackUtteranceAfterStrip(incoming, committed);
  }

  if (session.draftOpen && session.partialBuffer.trim()) {
    const buf = session.partialBuffer.trim();
    if (text.startsWith(buf) || buf.startsWith(text)) {
      text = text.length >= buf.length ? text : buf;
    } else {
      text = mergeUserSpeechText(buf, text);
    }
  }
  return sanitizeUserSpokenText(text);
}

/** Merge incremental partials within the same open utterance. */
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

function pickFinalUserText(previous: string, incoming: string): string {
  const inc = incoming.trim();
  const prev = previous.trim();
  if (!prev) return inc;
  if (inc.startsWith(prev) || prev.startsWith(inc)) {
    return inc.length >= prev.length ? inc : prev;
  }
  return inc;
}

function mergeAssistantText(
  previous: string,
  incoming: string,
  streaming: boolean,
): string {
  const inc = incoming.trim();
  if (!inc) return previous.trim();
  const prev = previous.trim();
  if (!streaming || !prev) return inc;
  if (inc === prev || inc.startsWith(prev)) return inc;
  if (prev.startsWith(inc)) return prev;
  return inc;
}

export function createTurn(
  turnId: string,
  userTurnSeq = 0,
  userMessageId = '',
): ChatTurn {
  const msgId = userMessageId || turnId;
  return {
    id: turnId,
    userMessageId: msgId,
    assistantMessageId: `${msgId}-assistant`,
    userTurnSeq,
    userText: '',
    userFinal: false,
    assistantText: '',
    assistantStreaming: false,
  };
}

function findTurnIndexBySeq(turns: ChatTurn[], turnSeq: number): number {
  if (turnSeq <= 0) return -1;
  return turns.findIndex((t) => t.userTurnSeq === turnSeq);
}

function findTurnByMessageId(turns: ChatTurn[], messageId: string): number {
  if (!messageId) return -1;
  return turns.findIndex((t) => t.userMessageId === messageId);
}

function findOpenDraftIndex(turns: ChatTurn[]): number {
  for (let i = turns.length - 1; i >= 0; i--) {
    if (!turns[i]!.userFinal) return i;
  }
  return -1;
}

function ensureSingleDraft(turns: ChatTurn[], keepIdx: number): ChatTurn[] {
  return turns.filter((t, i) => t.userFinal || i === keepIdx);
}

function findAssistantTurnIndex(turns: ChatTurn[]): number {
  for (let i = turns.length - 1; i >= 0; i--) {
    if (turns[i]!.userFinal) return i;
  }
  return turns.length > 0 ? turns.length - 1 : -1;
}

function nextUserMessageId(turnSeq: number, messageId?: string): string {
  if (messageId) return messageId;
  if (turnSeq > 0) return `user-msg-${turnSeq}`;
  return `user-msg-${Date.now()}`;
}

/** Backend bumped turn_seq — must not reuse the previous turn's draft/message id. */
function shouldForceNewDraft(
  session: UserTranscriptSession,
  turnSeq: number,
): boolean {
  if (turnSeq <= 0) return false;
  if (!session.draftOpen) {
    return (
      session.lastFinalizedTurnSeq > 0 && turnSeq > session.lastFinalizedTurnSeq
    );
  }
  return (
    session.lastFinalizedTurnSeq > 0 &&
    turnSeq > session.lastFinalizedTurnSeq &&
    turnSeq > session.activeTurnSeq
  );
}

function resetDraftSessionForNewSeq(
  session: UserTranscriptSession,
  turnSeq: number,
): UserTranscriptSession {
  return {
    ...session,
    activeUserMessageId: null,
    activeTurnSeq: turnSeq,
    partialBuffer: '',
    draftOpen: false,
    lastPartialNormalized: '',
  };
}

/** One finalized row per userTurnSeq; at most one draft at the end. */
export function collapseTurnsByUserSeq(turns: ChatTurn[]): ChatTurn[] {
  const out: ChatTurn[] = [];
  const finalSlotBySeq = new Map<number, number>();
  let draft: ChatTurn | null = null;

  for (const t of turns) {
    if (!t.userFinal) {
      draft = t;
      continue;
    }
    if (t.userTurnSeq <= 0) {
      out.push(t);
      continue;
    }
    const slot = finalSlotBySeq.get(t.userTurnSeq);
    if (slot == null) {
      finalSlotBySeq.set(t.userTurnSeq, out.length);
      out.push(t);
    } else {
      out[slot] = t;
    }
  }
  if (draft) out.push(draft);
  return out;
}

export type UserTranscriptAction =
  | 'USER_MESSAGE_CREATED'
  | 'USER_MESSAGE_UPDATED'
  | 'USER_MESSAGE_FINALIZED'
  | 'DUPLICATE_TRANSCRIPT_DROPPED';

export interface UserTranscriptResult {
  turns: ChatTurn[];
  session: UserTranscriptSession;
  action: UserTranscriptAction;
}

/**
 * Upsert user STT with session — one draft per utterance, strip prior-turn STT text.
 */
export function upsertUserTranscript(
  turns: ChatTurn[],
  session: UserTranscriptSession,
  text: string,
  isFinal: boolean,
  turnSeq?: number,
  messageId?: string,
): UserTranscriptResult {
  const trimmed = text.trim();
  if (!trimmed) {
    return { turns, session, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
  }

  const seq = turnSeq && turnSeq > 0 ? turnSeq : 0;
  const normIncoming = normalizeTranscriptDedupeKey(trimmed);

  if (!isFinal) {
    let nextSession = { ...session };
    if (shouldForceNewDraft(session, seq)) {
      nextSession = resetDraftSessionForNewSeq(session, seq);
    }

    let localized = localizeUserSttInput(trimmed, turns, nextSession);

    if (!localized.trim()) {
      return { turns, session: nextSession, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
    }

    if (isLeakyCumulativeUserText(localized, turns)) {
      const tail = fallbackUtteranceAfterStrip(
        trimmed,
        getCommittedUserTexts(turns),
      );
      if (!tail.trim() || isLeakyCumulativeUserText(tail, turns)) {
        return { turns, session: nextSession, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
      }
      localized = tail;
    }

    if (
      normIncoming === nextSession.lastPartialNormalized &&
      localized.length <= nextSession.partialBuffer.trim().length + 2
    ) {
      return { turns, session: nextSession, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
    }

    const next = [...turns];
    let idx = -1;
    if (!shouldForceNewDraft(session, seq)) {
      if (nextSession.activeUserMessageId) {
        idx = findTurnByMessageId(next, nextSession.activeUserMessageId);
      }
      if (idx < 0) idx = findOpenDraftIndex(next);
    }

    if (idx < 0) {
      const msgId = nextUserMessageId(seq, messageId);
      const id = seq > 0 ? `turn-${seq}-draft` : `turn-draft-${Date.now()}`;
      next.push(createTurn(id, seq, msgId));
      idx = next.length - 1;
      nextSession = {
        ...nextSession,
        activeUserMessageId: msgId,
        activeTurnSeq: Math.max(nextSession.activeTurnSeq, seq),
        draftOpen: true,
        partialBuffer: localized,
        lastPartialNormalized: normIncoming,
      };
      return {
        turns: collapseTurnsByUserSeq(
          ensureSingleDraft(assignDraft(next, idx, localized, seq, msgId), idx),
        ),
        session: nextSession,
        action: 'USER_MESSAGE_CREATED',
      };
    }

    const msgId =
      nextSession.activeUserMessageId || nextUserMessageId(seq, messageId);
    nextSession = {
      ...nextSession,
      activeUserMessageId: msgId,
      activeTurnSeq: seq > 0 ? seq : nextSession.activeTurnSeq,
      draftOpen: true,
      partialBuffer: localized,
      lastPartialNormalized: normIncoming,
    };
    return {
      turns: collapseTurnsByUserSeq(
        ensureSingleDraft(assignDraft(next, idx, localized, seq, msgId), idx),
      ),
      session: nextSession,
      action: 'USER_MESSAGE_UPDATED',
    };
  }

  const next = [...turns];
  const existingIdx = seq > 0 ? findTurnIndexBySeq(turns, seq) : -1;
  const localizedFinal = localizeUserSttInput(trimmed, turns, session, seq);
  let finalText = localizedFinal || trimmed;
  if (existingIdx >= 0 && turns[existingIdx]!.userFinal) {
    finalText = pickFinalUserText(turns[existingIdx]!.userText, finalText);
  }

  if (!finalText.trim()) {
    return { turns, session, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
  }

  if (isLeakyCumulativeUserText(finalText, turns)) {
    const tail = fallbackUtteranceAfterStrip(
      trimmed,
      getCommittedUserTexts(turns, seq),
    );
    if (!tail.trim() || isLeakyCumulativeUserText(tail, turns)) {
      return { turns, session, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
    }
    finalText = tail;
  }

  const committed = getCommittedUserTexts(turns, seq);
  const normFinal = normalizeTranscriptDedupeKey(finalText);
  if (
    committed.some(
      (c) => normalizeTranscriptDedupeKey(c) === normFinal,
    )
  ) {
    return { turns, session, action: 'DUPLICATE_TRANSCRIPT_DROPPED' };
  }

  let idx = session.activeUserMessageId
    ? findTurnByMessageId(next, session.activeUserMessageId)
    : -1;
  if (idx < 0 && seq > 0) idx = findTurnIndexBySeq(next, seq);
  const draftIdx = findOpenDraftIndex(next);
  if (idx < 0 && draftIdx >= 0) idx = draftIdx;

  const effectiveSeq = seq > 0 ? seq : session.activeTurnSeq;
  const msgId = nextUserMessageId(
    effectiveSeq,
    messageId || session.activeUserMessageId || undefined,
  );

  if (idx < 0) {
    const id = effectiveSeq > 0 ? `turn-${effectiveSeq}` : `turn-${Date.now()}`;
    next.push(createTurn(id, effectiveSeq, msgId));
    idx = next.length - 1;
  }

  const turn = next[idx]!;
  const stableId =
    effectiveSeq > 0 ? `turn-${effectiveSeq}` : turn.id;
  next[idx] = {
    ...turn,
    id: stableId,
    userMessageId: msgId,
    assistantMessageId: `${msgId}-assistant`,
    userTurnSeq: effectiveSeq > 0 ? effectiveSeq : turn.userTurnSeq,
    userText: finalText,
    userFinal: true,
  };

  const nextSession: UserTranscriptSession = {
    ...clearActiveUserTurn(session),
    lastFinalizedUserText: finalText,
    lastFinalizedTurnSeq:
      effectiveSeq > 0 ? effectiveSeq : session.lastFinalizedTurnSeq,
    lastPartialNormalized: '',
  };

  return {
    turns: collapseTurnsByUserSeq(ensureSingleDraft(next, idx)),
    session: nextSession,
    action: 'USER_MESSAGE_FINALIZED',
  };
}

function assignDraft(
  turns: ChatTurn[],
  idx: number,
  text: string,
  seq: number,
  messageId: string,
): ChatTurn[] {
  const turn = turns[idx]!;
  turns[idx] = {
    ...turn,
    userMessageId: messageId,
    assistantMessageId: `${messageId}-assistant`,
    userTurnSeq: seq > 0 ? seq : turn.userTurnSeq,
    userText: text,
    userFinal: false,
  };
  return turns;
}

/** @deprecated Use upsertUserTranscript with session — test helper. */
export function applyUserTranscript(
  turns: ChatTurn[],
  text: string,
  isFinal: boolean,
  turnSeq?: number,
): ChatTurn[] {
  let session = createUserTranscriptSession();
  let current = turns;
  const { turns: t, session: _s } = upsertUserTranscript(
    current,
    session,
    text,
    isFinal,
    turnSeq,
  );
  return t;
}

export function buildUserFinalDedupeKey(
  text: string,
  turnSeq?: number,
  messageId?: string,
): string {
  if (messageId) return `id:${messageId}`;
  const norm = normalizeTranscriptDedupeKey(text);
  if (turnSeq && turnSeq > 0) return `seq:${turnSeq}:${norm}`;
  return `text:${norm}`;
}

export function shouldSkipUserFinalCommit(
  seen: Set<string>,
  key: string,
  recentTimestamps: Map<string, number>,
  now = Date.now(),
): boolean {
  if (seen.has(key)) return true;
  const lastAt = recentTimestamps.get(key);
  if (lastAt != null && now - lastAt < FINAL_DEDUPE_WINDOW_MS) return true;
  return false;
}

export function recordUserFinalCommit(
  seen: Set<string>,
  recentTimestamps: Map<string, number>,
  key: string,
  now = Date.now(),
): void {
  seen.add(key);
  recentTimestamps.set(key, now);
  if (recentTimestamps.size > 64) {
    for (const [k, ts] of recentTimestamps) {
      if (now - ts > FINAL_DEDUPE_WINDOW_MS * 4) recentTimestamps.delete(k);
    }
  }
}

export type AssistantTranscriptAction =
  | 'ASSISTANT_MESSAGE_CREATED'
  | 'ASSISTANT_MESSAGE_UPDATED'
  | 'ASSISTANT_MESSAGE_FINALIZED'
  | 'ASSISTANT_EVENT_DROPPED_REASON';

export interface AssistantTranscriptResult {
  turns: ChatTurn[];
  session: UserTranscriptSession;
  action: AssistantTranscriptAction;
  dropReason?: string;
}

/** Upsert assistant LLM text on the latest finalized user turn. */
export function upsertAssistantTranscript(
  turns: ChatTurn[],
  session: UserTranscriptSession,
  text: string,
  streaming: boolean,
): AssistantTranscriptResult {
  const trimmed = text.trim();
  if (!trimmed) {
    return {
      turns,
      session,
      action: 'ASSISTANT_EVENT_DROPPED_REASON',
      dropReason: 'empty_text',
    };
  }

  const norm = normalizeTranscriptDedupeKey(trimmed);
  if (!streaming && norm === session.lastAssistantNormalized) {
    return {
      turns,
      session,
      action: 'ASSISTANT_EVENT_DROPPED_REASON',
      dropReason: 'duplicate_final',
    };
  }

  const next = [...turns];

  if (turns.length === 0) {
    const msgId = 'greeting-assistant';
    next.push({
      ...createTurn('turn-greeting', 0, 'greeting'),
      userFinal: true,
      assistantMessageId: msgId,
      assistantText: trimmed,
      assistantStreaming: streaming,
    });
    return {
      turns: next,
      session: { ...session, lastAssistantNormalized: norm },
      action: 'ASSISTANT_MESSAGE_CREATED',
    };
  }

  const idx = findAssistantTurnIndex(next);
  if (idx < 0) {
    return {
      turns,
      session,
      action: 'ASSISTANT_EVENT_DROPPED_REASON',
      dropReason: 'no_user_final_turn',
    };
  }

  const turn = next[idx]!;
  if (!turn.userFinal) {
    return {
      turns,
      session,
      action: 'ASSISTANT_EVENT_DROPPED_REASON',
      dropReason: 'user_turn_not_final',
    };
  }

  const merged = mergeAssistantText(turn.assistantText, trimmed, streaming);
  const unchanged =
    merged === turn.assistantText.trim() &&
    streaming === turn.assistantStreaming;

  if (unchanged) {
    return {
      turns,
      session,
      action: 'ASSISTANT_EVENT_DROPPED_REASON',
      dropReason: 'unchanged',
    };
  }

  const hadAssistant = Boolean(turn.assistantText.trim());
  next[idx] = {
    ...turn,
    assistantText: merged,
    assistantStreaming: streaming,
  };

  return {
    turns: next,
    session: { ...session, lastAssistantNormalized: norm },
    action: hadAssistant
      ? streaming
        ? 'ASSISTANT_MESSAGE_UPDATED'
        : 'ASSISTANT_MESSAGE_FINALIZED'
      : streaming
        ? 'ASSISTANT_MESSAGE_CREATED'
        : 'ASSISTANT_MESSAGE_FINALIZED',
  };
}

/** @deprecated test helper */
export function applyAssistantTranscript(
  turns: ChatTurn[],
  text: string,
  streaming: boolean,
): ChatTurn[] {
  const { turns: t } = upsertAssistantTranscript(
    turns,
    createUserTranscriptSession(),
    text,
    streaming,
  );
  return t;
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
  }
  return next;
}

/** Flatten turns into alternating user → assistant bubbles for the UI. */
export function turnsToBubbles(turns: ChatTurn[]): ChatBubble[] {
  const out: ChatBubble[] = [];
  for (const turn of turns) {
    if (
      !turn.userFinal &&
      isLeakyCumulativeUserText(turn.userText, turns)
    ) {
      continue;
    }
    const userDisplay = formatUserChatDisplayText(turn.userText);
    if (userDisplay) {
      out.push({
        id: `${turn.userMessageId}-user`,
        role: 'user',
        text: userDisplay,
        pending: !turn.userFinal,
      });
    }
    const assistantDisplay = formatChatDisplayText(turn.assistantText);
    if (assistantDisplay) {
      out.push({
        id: `${turn.assistantMessageId}`,
        role: 'llm',
        text: assistantDisplay,
        pending: turn.assistantStreaming,
      });
    }
  }
  return out;
}
