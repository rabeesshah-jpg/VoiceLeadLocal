import type { TranscriptState } from '../hooks/useAgentDataChannel';

interface Props {
  state: TranscriptState;
}

function roleLabel(role: 'user' | 'llm'): string {
  return role === 'user' ? 'You' : 'Assistant';
}

export default function TranscriptPanel({ state }: Props) {
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
