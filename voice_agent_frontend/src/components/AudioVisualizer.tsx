interface Props {
  agentState: string;
  /** True while the LiveKit room is up but the agent has not finished introducing itself. */
  awaitingIntroduction?: boolean;
}

/** Simple state indicator (no raw audio processing on client). */
export default function AudioVisualizer({
  agentState,
  awaitingIntroduction = false,
}: Props) {
  const active = agentState === 'speaking' || agentState === 'listening';
  const statusText =
    agentState === 'speaking'
      ? 'Agent speaking — speak to interrupt'
      : agentState === 'listening'
        ? 'Listening…'
        : agentState === 'thinking'
          ? 'Thinking…'
          : awaitingIntroduction
            ? 'Connecting…'
            : 'Waiting';

  return (
    <div className="chat-activity" aria-hidden>
      <div className={`chat-activity-bar ${active ? 'chat-activity-bar--active' : ''}`} />
      <p className="chat-activity-label">{statusText}</p>
    </div>
  );
}
