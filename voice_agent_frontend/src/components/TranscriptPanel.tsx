import { useEffect, useRef } from 'react';
import type { TranscriptState } from '../hooks/useAgentDataChannel';
import { logUiTelemetry } from '../lib/uiTelemetry';

interface Props {
  state: TranscriptState;
}

function roleLabel(role: 'user' | 'llm'): string {
  return role === 'user' ? 'You' : 'Assistant';
}

export default function TranscriptPanel({ state }: Props) {
  const renderedSigRef = useRef(new Set<string>());

  useEffect(() => {
    for (const bubble of state.bubbles) {
      const sig = `${bubble.id}:${bubble.pending ? 'pending' : 'final'}:${bubble.text}`;
      if (renderedSigRef.current.has(sig)) continue;
      renderedSigRef.current.add(sig);
      if (bubble.pending) continue;
      logUiTelemetry('transcript_rendered_on_ui', {
        role: bubble.role,
        bubble_id: bubble.id,
        pending: bubble.pending,
        text_preview: bubble.text.slice(0, 80),
        char_count: bubble.text.length,
      });
    }
  }, [state.bubbles]);

  if (state.bubbles.length === 0) {
    return null;
  }

  return (
    <div className="chat-messages">
      {state.bubbles.map((bubble) => (
        <div
          key={bubble.id}
          className={`chat-bubble chat-bubble--${bubble.role}${
            bubble.pending ? ' chat-bubble--pending' : ''
          }`}
        >
          <span className="chat-bubble-label">{roleLabel(bubble.role)}</span>
          <p className="chat-bubble-text">
            {bubble.text}
            {bubble.pending ? ' …' : ''}
          </p>
        </div>
      ))}
    </div>
  );
}
