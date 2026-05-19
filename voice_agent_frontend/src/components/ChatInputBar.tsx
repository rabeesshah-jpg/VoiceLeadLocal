import type { FormEvent } from 'react';
import { IconPhone, IconSend } from './icons';

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onCall: () => void;
  inCall: boolean;
  busy: boolean;
}

export default function ChatInputBar({
  value,
  onChange,
  onSend,
  onCall,
  inCall,
  busy,
}: Props) {
  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSend();
  };

  return (
    <form className="chat-input-bar" onSubmit={handleSubmit}>
      <input
        type="text"
        className="chat-input"
        placeholder="Type a message..."
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Message"
      />
      <button
        type="button"
        className={`chat-action-btn chat-action-btn--call ${inCall ? 'chat-action-btn--active' : ''}`}
        onClick={onCall}
        disabled={busy}
        aria-label={inCall ? 'End voice call' : 'Start voice call'}
      >
        <IconPhone />
      </button>
      <button
        type="submit"
        className="chat-action-btn chat-action-btn--send"
        disabled={busy || !value.trim()}
        aria-label="Send message"
        title="Voice conversations — use the call button to speak"
      >
        <IconSend />
      </button>
    </form>
  );
}
