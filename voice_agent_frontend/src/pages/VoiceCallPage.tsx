import { useCallback, useEffect, useState } from 'react';
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react';
import { endCall, getHealth, startCall, type StartCallResponse } from '../api/calls';
import { log, logError } from '../lib/logger';
import CallControls from '../components/CallControls';
import ConnectionBadge from '../components/ConnectionBadge';
import TranscriptPanel from '../components/TranscriptPanel';
import AudioVisualizer from '../components/AudioVisualizer';
import RoomDataBinder from '../components/RoomDataBinder';
import MuteSync from '../components/MuteSync';
import { useAgentDataChannel } from '../hooks/useAgentDataChannel';

export default function VoiceCallPage() {
  const [session, setSession] = useState<StartCallResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const { state: transcriptState, bindRoom, reset } = useAgentDataChannel();

  useEffect(() => {
    log('page_mount', {
      apiBase: import.meta.env.VITE_API_BASE_URL,
      hasDevKey: Boolean(import.meta.env.VITE_VOICE_AGENT_DEV_API_KEY),
    });
    getHealth().catch((e) => {
      logError('health_check_on_mount_failed', {
        message: e instanceof Error ? e.message : String(e),
      });
    });
  }, []);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setApiError(null);
    reset();
    log('ui_start_call_clicked');
    try {
      const s = await startCall();
      log('ui_livekit_connecting', {
        room: s.room_name,
        url: s.livekit_url,
      });
      setSession(s);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to start';
      logError('ui_start_call_failed', { message: msg });
      setApiError(msg);
    } finally {
      setBusy(false);
    }
  }, [reset]);

  const handleEnd = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    log('ui_end_call', { callId: session.call_id });
    try {
      await endCall(session.call_id);
    } catch (e) {
      logError('ui_end_call_failed', {
        message: e instanceof Error ? e.message : String(e),
      });
    }
    setSession(null);
    reset();
    setMuted(false);
    setBusy(false);
  }, [session, reset]);

  const inCall = Boolean(session);
  const connectionState = !session
    ? 'idle'
    : transcriptState.lastError
      ? 'error'
      : 'live';

  return (
    <>
      <h1>Voice Agent</h1>
      <p className="subtitle">
        LiveKit transport · server-side Deepgram · OpenRouter · Cartesia
      </p>

      {(apiError || transcriptState.lastError) && (
        <div className="error-banner">{apiError || transcriptState.lastError}</div>
      )}

      <div className="panel">
        <ConnectionBadge
          connection={connectionState as 'idle' | 'live' | 'error'}
          agentState={transcriptState.agentState}
        />
      </div>

      <CallControls
        inCall={inCall}
        muted={muted}
        busy={busy}
        onStart={handleStart}
        onEnd={handleEnd}
        onToggleMute={() => setMuted((m) => !m)}
      />

      {session && (
        <LiveKitRoom
          key={session.call_id}
          serverUrl={session.livekit_url}
          token={session.participant_token}
          connect
          audio
          video={false}
          onConnected={() => log('ui_livekit_connected', { room: session.room_name })}
          onDisconnected={() => {
            log('ui_livekit_disconnected', { room: session.room_name });
          }}
          onError={(err) => {
            logError('ui_livekit_error', { message: err.message });
            setApiError(err.message);
          }}
        >
          <RoomDataBinder bindRoom={bindRoom} />
          <MuteSync muted={muted} />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}

      <AudioVisualizer agentState={transcriptState.agentState} />
      <TranscriptPanel state={transcriptState} />
    </>
  );
}
