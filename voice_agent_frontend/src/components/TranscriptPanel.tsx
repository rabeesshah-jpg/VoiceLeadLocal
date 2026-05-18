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
    <div className="panel transcript">
      <div>
        <span className="label">You: </span>
        {userDisplay.length === 0 ? (
          <span style={{ color: '#9aa0a6' }}>—</span>
        ) : (
          userDisplay.map((line, i) => (
            <p key={`u-${i}`} style={{ margin: '0.25rem 0' }}>
              {line}
            </p>
          ))
        )}
      </div>
      <div style={{ marginTop: '1rem' }}>
        <span className="label agent">Agent: </span>
        {agentDisplay.length === 0 ? (
          <span style={{ color: '#9aa0a6' }}>—</span>
        ) : (
          agentDisplay.map((line, i) => (
            <p key={`a-${i}`} className="agent" style={{ margin: '0.25rem 0' }}>
              {line}
            </p>
          ))
        )}
      </div>
      {state.lastLatency && (
        <p className="latency" style={{ marginTop: '1rem' }}>
          Latency — STT: {state.lastLatency.stt_ms ?? '—'}ms · LLM:{' '}
          {state.lastLatency.llm_first_token_ms ?? '—'}ms · TTS:{' '}
          {state.lastLatency.tts_first_byte_ms ?? '—'}ms
        </p>
      )}
    </div>
  );
}
