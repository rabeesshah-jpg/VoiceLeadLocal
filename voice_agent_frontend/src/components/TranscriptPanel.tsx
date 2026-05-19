import type { TranscriptState } from '../hooks/useAgentDataChannel';

interface Props {
  state: TranscriptState;
}

export default function TranscriptPanel({ state }: Props) {
  const userDisplay = [
    ...state.userLines,
    ...(state.pendingUser && !state.userLines.includes(state.pendingUser)
      ? [`${state.pendingUser} …`]
      : []),
  ];
  const agentDisplay = [
    ...state.agentLines,
    ...(state.pendingAgent && !state.agentLines.includes(state.pendingAgent)
      ? [`${state.pendingAgent} …`]
      : []),
  ];

  return (
    <div className="chat-messages">
      {userDisplay.map((line, i) => (
        <div key={`u-${i}`} className="chat-bubble chat-bubble--user">
          {line}
        </div>
      ))}
      {agentDisplay.map((line, i) => (
        <div key={`a-${i}`} className="chat-bubble chat-bubble--agent">
          {line}
        </div>
      ))}
    </div>
  );
}
