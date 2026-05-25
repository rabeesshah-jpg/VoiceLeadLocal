import { describe, expect, it } from 'vitest';
import {
  applyAssistantTranscript,
  applyUserTranscript,
  turnsToBubbles,
} from './chatTurns';

describe('chatTurns', () => {
  it('alternates user then assistant per turn', () => {
    let turns = applyUserTranscript([], 'Hello', true);
    turns = applyAssistantTranscript(turns, 'Hi there [chuckle]', true);
    turns = applyUserTranscript(turns, "I'm also good", true);
    turns = applyAssistantTranscript(turns, 'Great!', false);

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user', 'llm']);
    expect(bubbles[1].text).toBe('Hi there');
    expect(bubbles[3].text).toBe('Great!');
    expect(bubbles[3].pending).toBe(false);
  });

  it('does not show assistant until user is final on that turn', () => {
    let turns = applyUserTranscript([], 'Hi', true);
    turns = applyAssistantTranscript(turns, 'Reply one', true);
    turns = applyUserTranscript(turns, 'Still talking', false);
    turns = applyAssistantTranscript(turns, 'Should not appear', true);

    const bubbles = turnsToBubbles(turns);
    expect(bubbles.map((b) => b.role)).toEqual(['user', 'llm', 'user']);
    expect(bubbles[2].pending).toBe(true);
  });
});
