import type { ConnectionBadgeState } from '../hooks/useLiveKitRoom';
import type { AgentUiState } from '../hooks/useAgentDataChannel';

interface Props {
  connection: ConnectionBadgeState;
  agentState: AgentUiState;
}

export default function ConnectionBadge({ connection, agentState }: Props) {
  const label =
    connection === 'live'
      ? `Live · Agent ${agentState}`
      : connection;

  return <span className={`badge badge-${connection}`}>{label}</span>;
}
