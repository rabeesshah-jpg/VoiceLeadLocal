import { useCallback, useEffect, useState } from 'react';
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react';
import { endCall, getHealth, startCall, type StartCallResponse } from '../api/calls';
import { log, logError } from '../lib/logger';
import TranscriptPanel from '../components/TranscriptPanel';
import AudioVisualizer from '../components/AudioVisualizer';
import RoomDataBinder from '../components/RoomDataBinder';
import MuteSync from '../components/MuteSync';
import PageHeader from '../components/PageHeader';
import IntroSection from '../components/IntroSection';
import ChatHeader from '../components/ChatHeader';
import ChatEmptyState from '../components/ChatEmptyState';
import ChatInputBar from '../components/ChatInputBar';
import PageFooter from '../components/PageFooter';
import LatencyPanel from '../components/LatencyPanel';
import { useAgentDataChannel } from '../hooks/useAgentDataChannel';

const VOICE_PROMPT = 'Start a voice conversation';

export default function VoiceCallPage() {
  const [session, setSession] = useState<StartCallResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState('');
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

  const hasMessages =
    transcriptState.userLines.length > 0 ||
    transcriptState.agentLines.length > 0 ||
    Boolean(transcriptState.pendingUser) ||
    Boolean(transcriptState.pendingAgent);

  const showEmptyState = !hasMessages && !inCall;

  const handleCallAction = useCallback(() => {
    if (inCall) {
      void handleEnd();
    } else {
      void handleStart();
    }
  }, [inCall, handleEnd, handleStart]);

  const handleSelectPrompt = useCallback(
    (text: string) => {
      if (text === VOICE_PROMPT) {
        void handleStart();
        return;
      }
      setInputValue(text);
    },
    [handleStart],
  );

  const handleSend = useCallback(() => {
    if (!inputValue.trim()) return;
    setInputValue('');
  }, [inputValue]);

  const displayError = apiError || transcriptState.lastError;

  return (
    <div className="page">
      <PageHeader />
      <IntroSection />

      <section className="chat-card" aria-label="Assistant chat">
        <ChatHeader
          connection={connectionState as 'idle' | 'live' | 'error'}
          agentState={transcriptState.agentState}
          inCall={inCall}
          muted={muted}
          onToggleMute={() => setMuted((m) => !m)}
        />

        {displayError && (
          <div className="error-banner" role="alert">
            {displayError}
          </div>
        )}

        <div className="chat-body">
          {showEmptyState ? (
            <ChatEmptyState onSelectPrompt={handleSelectPrompt} />
          ) : (
            <>
              {hasMessages && <TranscriptPanel state={transcriptState} />}
              {inCall && <AudioVisualizer agentState={transcriptState.agentState} />}
              {!hasMessages && inCall && (
                <p className="chat-body-hint">Voice call active — speak to begin.</p>
              )}
            </>
          )}
        </div>

        <div className="chat-footer-input">
          <ChatInputBar
            value={inputValue}
            onChange={setInputValue}
            onSend={handleSend}
            onCall={handleCallAction}
            inCall={inCall}
            busy={busy}
          />
          <p className="chat-powered-by">Powered by Voice Agent</p>
        </div>
      </section>

      {inCall && <LatencyPanel metrics={transcriptState.turnMetrics} />}

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

      <PageFooter />
    </div>
  );
}
