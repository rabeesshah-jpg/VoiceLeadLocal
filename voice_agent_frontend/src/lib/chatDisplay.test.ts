import { describe, expect, it } from 'vitest';
import { formatChatDisplayText, formatUserChatDisplayText } from './chatDisplay';
import { appendUserMessage } from './llmTranscript';

describe('formatChatDisplayText', () => {
  it('removes supertonic and chatterbox tags', () => {
    expect(formatChatDisplayText('Hi <breath>, how are you?')).toBe('Hi, how are you?');
    expect(formatChatDisplayText('Well [sigh] ok <laugh>')).toBe('Well ok');
    expect(formatChatDisplayText('[clear throat] One moment')).toBe('One moment');
  });
});

describe('formatUserChatDisplayText', () => {
  it('removes bracket STT artifacts', () => {
    expect(
      formatUserChatDisplayText(
        'I want a bit completely commercialized. [BLANK_AUDIO]',
      ),
    ).toBe('I want a bit completely commercialized.');
  });

  it('removes parenthetical paralinguistics', () => {
    expect(formatUserChatDisplayText('My name is Omar. (laughs)')).toBe(
      'My name is Omar.',
    );
  });

  it('removes common hallucination phrases', () => {
    expect(formatUserChatDisplayText('Thanks for watching.')).toBe('');
  });

  it('removes trailing filler fragments', () => {
    expect(
      formatUserChatDisplayText(
        'I want to build a website, can you help me in this? No.',
      ),
    ).toBe('I want to build a website, can you help me in this?');
    expect(formatUserChatDisplayText("I'm in Dubai. - Yeah.")).toBe(
      "I'm in Dubai.",
    );
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
