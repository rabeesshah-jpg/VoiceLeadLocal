import { IconChatBubble } from './icons';

export const SUGGESTED_PROMPTS = [
  'What can you help me with?',
  'Explain this project',
  'Start a voice conversation',
  'How does this assistant work?',
] as const;

interface Props {
  onSelectPrompt: (text: string) => void;
}

export default function ChatEmptyState({ onSelectPrompt }: Props) {
  return (
    <div className="chat-empty">
      <div className="chat-empty-icon" aria-hidden>
        <IconChatBubble />
      </div>
      <h2 className="chat-empty-title">Ask me anything</h2>
      <p className="chat-empty-helper">
        Start a voice conversation or pick a suggestion below to get going.
      </p>
      <div className="prompt-chips">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="prompt-chip"
            onClick={() => onSelectPrompt(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}
