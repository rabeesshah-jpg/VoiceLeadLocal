import { describe, expect, it } from 'vitest';
import {
  applyAssistantTranscript,
  buildUserFinalDedupeKey,
  createTurn,
  createUserTranscriptSession,
  finishAssistantStreaming,
  mergeUserSpeechText,
  recordUserFinalCommit,
  shouldSkipUserFinalCommit,
  discardOpenUserDraft,
  isLeakyCumulativeUserText,
  stripAllCommittedUserTexts,
  stripPriorUserTextFromCumulative,
  turnsToBubbles,
  upsertAssistantTranscript,
  upsertUserTranscript,
} from './chatTurns';

function chainUser(
  turns: ReturnType<typeof upsertUserTranscript>['turns'],
  session: ReturnType<typeof createUserTranscriptSession>,
  text: string,
  isFinal: boolean,
  turnSeq?: number,
) {
  const r = upsertUserTranscript(turns, session, text, isFinal, turnSeq);
  return { turns: r.turns, session: r.session };
}

describe('chatTurns', () => {
  it('shows opening greeting before any user speech', () => {
    const session = createUserTranscriptSession();
    const { turns } = upsertAssistantTranscript(
      [],
      session,
      'Hello, I am Noura.',
      true,
    );
    const bubbles = turnsToBubbles(turns);
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].role).toBe('llm');
    expect(bubbles[0].text).toContain('Noura');
  });

  it('alternates user then assistant per turn', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hello', true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Hi there [chuckle]', true));
    ({ turns, session } = chainUser(turns, session, "I'm also good", true, 2));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Great!', false));

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user', 'llm']);
    expect(bubbles[1].text).toBe('Hi there');
    expect(bubbles[3].text).toBe('Great!');
    expect(bubbles[3].pending).toBe(false);
  });

  it('accumulates incremental STT finals into one user line (same turn_seq)', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hello', true, 1));
    ({ turns, session } = chainUser(turns, session, 'Hello world', true, 1));
    const bubbles = turnsToBubbles(turns);
    expect(bubbles[0].text).toBe('Hello world');
    expect(bubbles[0].role).toBe('user');
    expect(bubbles.filter((b) => b.role === 'user')).toHaveLength(1);
  });

  it('keeps one user line when assistant streams before a later STT final', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hello', true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Hi', true));
    ({ turns, session } = chainUser(turns, session, 'Hello world', true, 1));
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'user')).toHaveLength(1);
    expect(bubbles[0].text).toBe('Hello world');
  });

  it('shows first user message from final-only transcript', () => {
    let session = createUserTranscriptSession();
    const { turns } = chainUser([], session, 'First thing I said', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].text).toBe('First thing I said');
    expect(bubbles[0].pending).toBe(false);
  });

  it('shows pending user bubble before final', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hello', false));
    const pending = turnsToBubbles(turns);
    expect(pending[0].pending).toBe(true);
    ({ turns, session } = chainUser(turns, session, 'Hello', true, 1));
    const done = turnsToBubbles(turns);
    expect(done[0].pending).toBe(false);
  });

  it('mergeUserSpeechText appends non-overlapping segments', () => {
    expect(mergeUserSpeechText('Hello', 'world')).toBe('Hello world');
    expect(mergeUserSpeechText('Hello', 'Hello world')).toBe('Hello world');
  });

  it('strips prior finalized text from cumulative STT on new turn', () => {
    const prior =
      'Okay, how are you about yourself. want to build a website can you help me in this';
    const cumulative = `${prior} my name is Rabi's`;
    expect(stripPriorUserTextFromCumulative(cumulative, prior)).toBe(
      "my name is Rabi's",
    );
  });

  it('keeps one draft user bubble across partials with changing turn_seq', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hello', false, 1));
    ({ turns, session } = chainUser(turns, session, 'Hello world', false, 2));
    ({ turns, session } = chainUser(
      turns,
      session,
      'Hello world again',
      false,
      3,
    ));
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'user')).toHaveLength(1);
    expect(bubbles[0].text).toBe('Hello world again');
    expect(bubbles[0].pending).toBe(true);
  });

  it('new turn after final does not include previous user text', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    const first =
      'Okay, how are you about yourself. want to build a website can you help me in this';
    ({ turns, session } = chainUser(turns, session, first, true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Sure, I can help.', true));
    const cumulative = `${first} Hey, how are you?`;
    ({ turns, session } = chainUser(turns, session, cumulative, true, 2));
    const bubbles = turnsToBubbles(turns);
    const users = bubbles.filter((b) => b.role === 'user');
    expect(users).toHaveLength(2);
    expect(users[0].text).toBe(first);
    expect(users[1].text).toBe('Hey, how are you?');
  });

  it('stripAllCommittedUserTexts removes multiple prior finals', () => {
    const committed = [
      'How are you me about yourself. want to build a website',
      'How are you me about yourself.',
    ];
    const incoming = `${committed[0]} and we are going`;
    expect(stripAllCommittedUserTexts(incoming, committed)).toBe('and we are going');
  });

  it('starts a new draft when turn_seq advances after a final', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'First turn done', true, 5));
    ({ turns } = upsertAssistantTranscript(turns, session, 'OK', true));
    ({ turns, session } = chainUser(
      turns,
      session,
      'First turn done Hello there',
      false,
      6,
    ));
    const bubbles = turnsToBubbles(turns);
    const users = bubbles.filter((b) => b.role === 'user');
    expect(users).toHaveLength(2);
    expect(users[1].text).toBe('Hello there');
    expect(users[1].id).toBe('user-msg-6-user');
  });

  it('partials after a final strip prior turns even when turn_seq unchanged', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    const first = 'My name is Ali from Darkness';
    ({ turns, session } = chainUser(turns, session, first, true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Thanks Ali.', true));
    ({ turns, session } = chainUser(
      turns,
      session,
      `${first} and we are going to be`,
      false,
      1,
    ));
    const bubbles = turnsToBubbles(turns);
    const users = bubbles.filter((b) => b.role === 'user');
    expect(users).toHaveLength(2);
    expect(users[1].text).toBe('and we are going to be');
    expect(users[1].pending).toBe(true);
  });

  it('streaming assistant updates one bubble until final', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Question', true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Answer part', true));
    ({ turns } = upsertAssistantTranscript(
      turns,
      session,
      'Answer part two',
      true,
    ));
    ({ turns } = upsertAssistantTranscript(
      turns,
      session,
      'Answer part two',
      false,
    ));
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'llm')).toHaveLength(1);
    expect(bubbles[1].text).toBe('Answer part two');
    expect(bubbles[1].pending).toBe(false);
  });

  it('skips duplicate user final within dedupe window', () => {
    const seen = new Set<string>();
    const recent = new Map<string, number>();
    const key = buildUserFinalDedupeKey('Hello there', 1, 'msg-1');
    expect(shouldSkipUserFinalCommit(seen, key, recent, 1000)).toBe(false);
    recordUserFinalCommit(seen, recent, key, 1000);
    expect(shouldSkipUserFinalCommit(seen, key, recent, 1500)).toBe(true);
  });

  it('does not show assistant until user is final on that turn', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hi', true, 1));
    ({ turns } = upsertAssistantTranscript(turns, session, 'Reply one', true));
    ({ turns, session } = chainUser(turns, session, 'Still talking', false));
    ({ turns } = upsertAssistantTranscript(
      turns,
      session,
      'Should not appear',
      true,
    ));

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user']);
    expect(bubbles[2].pending).toBe(true);
  });

  it('detects leaky cumulative user text', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(
      turns,
      session,
      'Yeah want to schedule a call',
      true,
      1,
    ));
    ({ turns, session } = chainUser(turns, session, 'Okay', true, 2));
    const leaky =
      'Yeah want to schedule a call Okay and more garbage from STT buffer';
    expect(isLeakyCumulativeUserText(leaky, turns)).toBe(true);
  });

  it('discards open draft so only finalized user rows remain', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Done', true, 1));
    turns.push({
      ...createTurn('turn-2-draft', 2, 'user-msg-2'),
      userText: 'junk cumulative draft',
      userFinal: false,
    });
    const collapsed = discardOpenUserDraft(turns);
    expect(collapsed.filter((t) => !t.userFinal)).toHaveLength(0);
    expect(turnsToBubbles(collapsed).filter((b) => b.role === 'user')).toHaveLength(
      1,
    );
  });

  it('dedupes identical assistant snapshots', () => {
    let session = createUserTranscriptSession();
    let turns: ReturnType<typeof upsertUserTranscript>['turns'] = [];
    ({ turns, session } = chainUser(turns, session, 'Hi', true, 1));
    turns = applyAssistantTranscript(turns, 'Same reply', true);
    turns = applyAssistantTranscript(turns, 'Same reply', false);
    turns = finishAssistantStreaming(turns);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'llm')).toHaveLength(1);
    expect(bubbles[1].pending).toBe(false);
  });
});
