import { describe, expect, it } from 'vitest';
import {
  applyAssistantTranscript,
  applyUserTranscript,
  finishAssistantStreaming,
  mergeUserSpeechText,
  turnsToBubbles,
} from './chatTurns';

describe('chatTurns', () => {
  it('shows opening greeting before any user speech', () => {
    const turns = applyAssistantTranscript([], 'Hello, I am Noura.', true);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].role).toBe('llm');
    expect(bubbles[0].text).toContain('Noura');
  });

  it('alternates user then assistant per turn', () => {
    let turns = applyUserTranscript([], 'Hello', true, 1);
    turns = applyAssistantTranscript(turns, 'Hi there [chuckle]', true);
    turns = applyUserTranscript(turns, "I'm also good", true, 2);
    turns = applyAssistantTranscript(turns, 'Great!', false);

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user', 'llm']);
    expect(bubbles[1].text).toBe('Hi there');
    expect(bubbles[3].text).toBe('Great!');
    expect(bubbles[3].pending).toBe(false);
  });

  it('accumulates incremental STT finals into one user line (same turn_seq)', () => {
    let turns = applyUserTranscript([], 'Hello', true, 1);
    turns = applyUserTranscript(turns, 'Hello world', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles[0].text).toBe('Hello world');
    expect(bubbles[0].role).toBe('user');
    expect(bubbles).toHaveLength(1);
  });

  it('keeps one user line when assistant streams before a later STT final', () => {
    let turns = applyUserTranscript([], 'Hello', true, 1);
    turns = applyAssistantTranscript(turns, 'Hi', true);
    turns = applyUserTranscript(turns, 'Hello world', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'user')).toHaveLength(1);
    expect(bubbles[0].text).toBe('Hello world');
  });

  it('shows first user message from final-only transcript', () => {
    const turns = applyUserTranscript([], 'First thing I said', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].text).toBe('First thing I said');
    expect(bubbles[0].pending).toBe(false);
  });

  it('shows pending user bubble before final', () => {
    let turns = applyUserTranscript([], 'Hello', false);
    const pending = turnsToBubbles(turns);
    expect(pending[0].pending).toBe(true);
    turns = applyUserTranscript(turns, 'Hello', true, 1);
    const done = turnsToBubbles(turns);
    expect(done[0].pending).toBe(false);
  });

  it('mergeUserSpeechText appends non-overlapping segments', () => {
    expect(mergeUserSpeechText('Hello', 'world')).toBe('Hello world');
    expect(mergeUserSpeechText('Hello', 'Hello world')).toBe('Hello world');
  });

  it('does not show assistant until user is final on that turn', () => {
    let turns = applyUserTranscript([], 'Hi', true, 1);
    turns = applyAssistantTranscript(turns, 'Reply one', true);
    turns = applyUserTranscript(turns, 'Still talking', false);
    turns = applyAssistantTranscript(turns, 'Should not appear', true);

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user']);
    expect(bubbles[2].pending).toBe(true);
  });

  it('replaces user text on final update (e.g. English translation)', () => {
    let turns = applyUserTranscript([], '…', true, 1);
    turns = applyUserTranscript(turns, 'Hello, I need a website', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles[0].text).toBe('Hello, I need a website');
  });

  it('shows assistant on first turn when user final arrives before llm', () => {
    let turns = applyUserTranscript([], '…', true, 1);
    turns = applyAssistantTranscript(turns, 'مرحباً', true);
    turns = applyUserTranscript(turns, 'I need help', true, 1);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm']);
    expect(bubbles[0].text).toBe('I need help');
    expect(bubbles[1].text).toBe('مرحباً');
  });

  it('dedupes identical assistant snapshots', () => {
    let turns = applyUserTranscript([], 'Hi', true, 1);
    turns = applyAssistantTranscript(turns, 'Same reply', true);
    turns = applyAssistantTranscript(turns, 'Same reply', false);
    turns = finishAssistantStreaming(turns);
    const bubbles = turnsToBubbles(turns);
    expect(bubbles.filter((b) => b.role === 'llm')).toHaveLength(1);
    expect(bubbles[1].pending).toBe(false);
  });
});
