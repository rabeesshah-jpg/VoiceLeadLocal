import { useCallback, useEffect, useState } from 'react';
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react';
import { endCall, getHealth, startCall, type StartCallResponse } from '../api/calls';
import { log, logError } from '../lib/logger';
import { setUiTelemetryCallContext } from '../lib/uiTelemetry';
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
import BrowserAudioTelemetry from '../components/BrowserAudioTelemetry';
import MuteSync from '../components/MuteSync';
import PageHeader from '../components/PageHeader';
import IntroSection from '../components/IntroSection';
import ChatHeader from '../components/ChatHeader';
import CallEmptyState from '../components/CallEmptyState';
import CallFooter, { type VoiceMode } from '../components/CallFooter';
import { isVoiceProfileUsable, type VoiceProfile } from '../api/voiceProfiles';
import PageFooter from '../components/PageFooter';
import LatencyPanel from '../components/LatencyPanel';
import { useAgentDataChannel } from '../hooks/useAgentDataChannel';

export default function VoiceCallPage() {
  const [session, setSession] = useState<StartCallResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [muted, setMuted] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [voiceMode, setVoiceMode] = useState<VoiceMode>('preset');
  const [voice, setVoice] = useState<VoiceGender>(DEFAULT_VOICE_GENDER);
  const [voiceProfileId, setVoiceProfileId] = useState<string | null>(null);
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([]);
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
    const fallbackVoice = VOICE_PRESETS[voice].tts.voice;
    const selectedProfile = voiceProfiles.find((p) => p.id === voiceProfileId);
    const providerVoiceId = selectedProfile?.provider_voice_id || null;
    const startPayload = {
      persona_id: voice,
      language,
      voice_mode: voiceMode,
      ...(voiceMode === 'custom' && voiceProfileId
        ? { voice_profile_id: voiceProfileId, fallback_voice: fallbackVoice }
        : {}),
    };
    log('ui_start_call_clicked', {
      voiceMode,
      voice,
      voiceProfileId,
      providerVoiceId,
      language,
      tts: VOICE_PRESETS[voice].tts,
      fallbackVoice,
      startPayload,
    });
    if (voiceMode === 'custom') {
      log('START_CALL_CUSTOM_VOICE_PAYLOAD', startPayload);
    }
    try {
      const s = await startCall(startPayload);
      log('ui_livekit_connecting', {
        room: s.room_name,
        url: s.livekit_url,
        voice,
        language,
        voiceConfig: s.voice ?? null,
      });
      setSession(s);
      setUiTelemetryCallContext(s.call_id, s.room_name);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to start';
      logError('ui_start_call_failed', { message: msg });
      setApiError(msg);
    } finally {
      setBusy(false);
    }
  }, [reset, voice, language, voiceMode, voiceProfileId, voiceProfiles]);

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
    setUiTelemetryCallContext(null, null);
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

  const selectedProfile = voiceProfiles.find((p) => p.id === voiceProfileId);
  const profileUsable = selectedProfile ? isVoiceProfileUsable(selectedProfile) : false;
  const customVoiceBlocked =
    voiceMode === 'custom' && (!voiceProfileId || !profileUsable);
  const startDisabledReason = customVoiceBlocked
    ? !voiceProfileId
      ? 'Select or create a voice profile before starting a call.'
      : 'Voice profile is still processing or not ready.'
    : null;

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
          voiceMode={voiceMode}
          onVoiceModeChange={setVoiceMode}
          voice={voice}
          onVoiceChange={setVoice}
          voiceProfileId={voiceProfileId}
          onVoiceProfileChange={setVoiceProfileId}
          voiceProfiles={voiceProfiles}
          onVoiceProfilesChange={setVoiceProfiles}
          language={language}
          onLanguageChange={setLanguage}
          inCall={inCall}
          busy={busy}
          startDisabled={customVoiceBlocked}
          startDisabledReason={startDisabledReason}
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
          <BrowserAudioTelemetry />
          <MuteSync muted={muted} />
          <RoomAudioRenderer />
        </LiveKitRoom>
      )}

      <PageFooter />
    </div>
  );
}
