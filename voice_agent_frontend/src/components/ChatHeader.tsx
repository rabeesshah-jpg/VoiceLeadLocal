import type { ConnectionBadgeState } from '../hooks/useLiveKitRoom';
import type { AgentUiState } from '../hooks/useAgentDataChannel';
import { IconAssistant } from './icons';

interface Props {
  connection: ConnectionBadgeState;
  agentState: AgentUiState;
  inCall: boolean;
  muted: boolean;
  onToggleMute: () => void;
}

export default function ChatHeader({
  connection,
  agentState,
  inCall,
  muted,
  onToggleMute,
}: Props) {
  const isOnline = connection === 'live';
  const statusLabel = isOnline
    ? 'Online'
    : connection === 'error'
      ? 'Error'
      : inCall
        ? 'Connecting'
        : 'Ready';

  return (
    <div className="chat-header">
      <div className="chat-header-left">
        <div className="chat-avatar" aria-hidden>
          <IconAssistant />
        </div>
        <div className="chat-header-titles">
          <span className="chat-title">Agent Nora</span>
          <span className="chat-subtitle">Speak To Connect</span>
        </div>
      </div>
      <div className="chat-header-right">
        {inCall && (
          <button
            type="button"
            className="chat-mute-btn"
            onClick={onToggleMute}
            aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
        )}
        <span
          className={`chat-status ${isOnline ? 'chat-status--online' : ''}`}
          title={agentState !== 'idle' ? `Agent: ${agentState}` : undefined}
        >
          <span className="chat-status-dot" aria-hidden />
          {statusLabel}
        </span>
      </div>
    </div>
  );
}
