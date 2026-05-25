import { useCallback, useEffect, useState } from 'react';
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react';
import { endCall, getHealth, startCall, type StartCallResponse } from '../api/calls';
import { log, logError } from '../lib/logger';
import {
  DEFAULT_CALL_LANGUAGE,
  type CallLanguage,
} from '../lib/callLanguage';
import {
  DEFAULT_VOICE_GENDER,
  type VoiceGender,
  VOICE_PRESETS,
} from '../lib/voicePresets';
import TranscriptPanel from '../components/TranscriptPanel';
import AudioVisualizer from '../components/AudioVisualizer';
import RoomDataBinder from '../components/RoomDataBinder';
import MuteSync from '../components/MuteSync';
import PageHeader from '../components/PageHeader';
import IntroSection from '../components/IntroSection';
import ChatHeader from '../components/ChatHeader';
import CallEmptyState from '../components/CallEmptyState';
import CallFooter from '../components/CallFooter';
import PageFooter from '../components/PageFooter';
import LatencyPanel from '../components/LatencyPanel';
import { useAgentDataChannel } from '../hooks/useAgentDataChannel';

export default function VoiceCallPage() {
  const [session, setSession] = useState<StartCallResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [voice, setVoice] = useState<VoiceGender>(DEFAULT_VOICE_GENDER);
  const [language, setLanguage] = useState<CallLanguage>(DEFAULT_CALL_LANGUAGE);
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
    log('ui_start_call_clicked', {
      voice,
      language,
      tts: VOICE_PRESETS[voice].tts,
    });
    try {
      const s = await startCall({ persona_id: voice, language });
      log('ui_livekit_connecting', {
        room: s.room_name,
        url: s.livekit_url,
        voice,
        language,
      });
      setSession(s);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to start';
      logError('ui_start_call_failed', { message: msg });
      setApiError(msg);
    } finally {
      setBusy(false);
    }
  }, [reset, voice, language]);

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

  const hasMessages = transcriptState.bubbles.length > 0;

  const showEmptyState = !hasMessages && !inCall;

  const handleCallAction = useCallback(() => {
    if (inCall) {
      void handleEnd();
    } else {
      void handleStart();
    }
  }, [inCall, handleEnd, handleStart]);

  const displayError = apiError || transcriptState.lastError;

  return (
    <div className="page">
      <PageHeader />
      <IntroSection />

      <section className="chat-card" aria-label="Voice calling agent">
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
            <CallEmptyState />
          ) : (
            <>
              {hasMessages && <TranscriptPanel state={transcriptState} />}
              {inCall && (
                <AudioVisualizer
                  agentState={transcriptState.agentState}
                  awaitingIntroduction={transcriptState.agentState === 'idle'}
                />
              )}
              {!hasMessages && inCall && transcriptState.agentState === 'idle' && (
                <p className="chat-body-hint">Connecting to agent…</p>
              )}
            </>
          )}
        </div>

        <CallFooter
          voice={voice}
          onVoiceChange={setVoice}
          language={language}
          onLanguageChange={setLanguage}
          inCall={inCall}
          busy={busy}
          onCall={handleCallAction}
        />
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
