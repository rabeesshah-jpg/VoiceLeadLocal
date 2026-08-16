import { IconPhone } from './icons';

export default function CallEmptyState() {
  return (
    <div className="chat-empty">
      <div className="chat-empty-icon chat-empty-icon--call" aria-hidden>
        <IconPhone />
      </div>
      <h2 className="chat-empty-title">Agent Nora</h2>
      <p className="chat-empty-helper">
        Choose a voice below, then tap <strong>Start call</strong> and speak naturally.
        No typing needed anymore, your words appear in the transcript as you talk.
      </p>
    </div>
  );
}
