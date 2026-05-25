import { describe, expect, it } from 'vitest';
import { removeActiveLlmMessage, upsertActiveLlmMessage } from './llmTranscript';

describe('llmTranscript', () => {
  it('creates then updates the active llm bubble', () => {
    const first = upsertActiveLlmMessage([], null, 'Hello');
    expect(first.messages).toHaveLength(1);
    expect(first.messages[0].text).toBe('Hello');

    const second = upsertActiveLlmMessage(
      first.messages,
      first.activeId,
      'Hello [chuckle] there',
    );
    expect(second.messages).toHaveLength(1);
    expect(second.messages[0].text).toBe('Hello [chuckle] there');
    expect(second.activeId).toBe(first.activeId);
  });

  it('removes only the active llm bubble', () => {
    const user = { id: 'u1', role: 'user' as const, text: 'Hi' } satisfies import('./llmTranscript').ChatLine;
    const upserted = upsertActiveLlmMessage([user], null, 'Reply');
    const next = removeActiveLlmMessage(upserted.messages, upserted.activeId);
    expect(next).toEqual([user]);
  });
});
