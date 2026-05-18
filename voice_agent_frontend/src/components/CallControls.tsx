interface Props {
  inCall: boolean;
  muted: boolean;
  busy: boolean;
  onStart: () => void;
  onEnd: () => void;
  onToggleMute: () => void;
}

export default function CallControls({
  inCall,
  muted,
  busy,
  onStart,
  onEnd,
  onToggleMute,
}: Props) {
  return (
    <div className="controls">
      {!inCall ? (
        <button type="button" className="primary" disabled={busy} onClick={onStart}>
          Start call
        </button>
      ) : (
        <>
          <button type="button" className="secondary" onClick={onToggleMute}>
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={onEnd}>
            End call
          </button>
        </>
      )}
    </div>
  );
}
