import { describe, expect, it } from 'vitest';
import { formatChatDisplayText } from './chatDisplay';
import { appendUserMessage } from './llmTranscript';

describe('formatChatDisplayText', () => {
  it('removes supertonic and chatterbox tags', () => {
    expect(formatChatDisplayText('Hi <breath>, how are you?')).toBe('Hi, how are you?');
    expect(formatChatDisplayText('Well [sigh] ok <laugh>')).toBe('Well ok');
    expect(formatChatDisplayText('[clear throat] One moment')).toBe('One moment');
  });
});

describe('appendUserMessage', () => {
  it('places user before an early LLM bubble', () => {
    const llm = { id: 'llm-1', role: 'llm' as const, text: 'Reply' };
    const user = { id: 'u-1', role: 'user' as const, text: 'Hello' };
    const ordered = appendUserMessage([llm], user, 'llm-1');
    expect(ordered.map((m) => m.role)).toEqual(['user', 'llm']);
  });

  it('appends user after a completed turn', () => {
    const messages = [
      { id: 'u1', role: 'user' as const, text: 'A' },
      { id: 'l1', role: 'llm' as const, text: 'B' },
    ];
    const user = { id: 'u2', role: 'user' as const, text: 'C' };
    const ordered = appendUserMessage(messages, user, 'llm-2');
    expect(ordered.map((m) => m.role)).toEqual(['user', 'llm', 'user']);
  });
});
