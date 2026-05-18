interface Props {
  agentState: string;
}

/** Simple state indicator (no raw audio processing on client). */
export default function AudioVisualizer({ agentState }: Props) {
  const active = agentState === 'speaking' || agentState === 'listening';
  return (
    <div className="panel" aria-hidden>
      <div
        style={{
          height: 8,
          borderRadius: 4,
          background: active
            ? 'linear-gradient(90deg, #8ab4f8, #81c995)'
            : '#2a2f3a',
          transition: 'background 0.2s',
        }}
      />
      <p className="latency" style={{ marginTop: '0.5rem', marginBottom: 0 }}>
        {agentState === 'speaking'
          ? 'Agent speaking — speak to interrupt'
          : agentState === 'listening'
            ? 'Listening…'
            : agentState === 'thinking'
              ? 'Thinking…'
              : 'Waiting'}
      </p>
    </div>
  );
}
