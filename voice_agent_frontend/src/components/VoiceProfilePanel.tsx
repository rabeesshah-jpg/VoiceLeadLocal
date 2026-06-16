import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cloneVoiceProfile,
  deleteVoiceProfile,
  isVoiceProfileUsable,
  listVoiceProfiles,
  refreshVoiceProfile,
  testVoiceProfile,
  uploadJsonVoiceProfile,
  type VoiceProfile,
} from '../api/voiceProfiles';
import { log, logError } from '../lib/logger';

export type VoicePanelPhase =
  | 'idle'
  | 'recording'
  | 'recorded'
  | 'uploading'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'testing';

interface Props {
  selectedProfileId: string | null;
  onSelectProfile: (profileId: string | null) => void;
  disabled?: boolean;
  onProfilesChange?: (profiles: VoiceProfile[]) => void;
}

const DEFAULT_RECORD_SECONDS = 8;

export default function VoiceProfilePanel({
  selectedProfileId,
  onSelectProfile,
  disabled = false,
  onProfilesChange,
}: Props) {
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [capabilitiesLoaded, setCapabilitiesLoaded] = useState(false);
  const [cloningEnabled, setCloningEnabled] = useState(false);
  const [supportsAudioClone, setSupportsAudioClone] = useState(false);
  const [supportsJsonUpload, setSupportsJsonUpload] = useState(false);
  const [capabilitiesMessage, setCapabilitiesMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [phase, setPhase] = useState<VoicePanelPhase>('idle');
  const [profileName, setProfileName] = useState('');
  const [jsonDisplayName, setJsonDisplayName] = useState('');
  const [jsonFile, setJsonFile] = useState<File | null>(null);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [testAudioUrl, setTestAudioUrl] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const didAutoSelectRef = useRef(false);
  const jsonInputRef = useRef<HTMLInputElement | null>(null);

  const updateProfiles = useCallback(
    (next: VoiceProfile[]) => {
      setProfiles(next);
      onProfilesChange?.(next);
    },
    [onProfilesChange],
  );

  const refreshProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listVoiceProfiles();
      setCloningEnabled(Boolean(data.voice_cloning_enabled));
      setSupportsAudioClone(Boolean(data.supports_reference_audio_cloning));
      setSupportsJsonUpload(Boolean(data.supports_voice_builder_json));
      setCapabilitiesMessage(data.capabilities_message?.trim() || '');
      setCapabilitiesLoaded(true);
      updateProfiles(data.profiles);
      if (!didAutoSelectRef.current) {
        const defaultProfile = data.profiles.find((p) => p.is_default && isVoiceProfileUsable(p));
        if (defaultProfile) {
          didAutoSelectRef.current = true;
          onSelectProfile(defaultProfile.id);
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load voice profiles';
      setError(msg);
      logError('voice_profiles_load_failed', { message: msg });
    } finally {
      setLoading(false);
    }
  }, [onSelectProfile, updateProfiles]);

  useEffect(() => {
    void refreshProfiles();
  }, [refreshProfiles]);

  useEffect(() => {
    const processing = profiles.filter((p) => p.status === 'processing');
    if (processing.length === 0 || disabled) return;

    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const refreshed = await Promise.all(
            processing.map((p) => refreshVoiceProfile(p.id)),
          );
          const byId = new Map(refreshed.map((p) => [p.id, p]));
          const merged = profiles.map((p) => byId.get(p.id) ?? p);
          updateProfiles(merged);
          const selected = selectedProfileId ? byId.get(selectedProfileId) : null;
          if (selected && isVoiceProfileUsable(selected)) {
            setPhase('idle');
            setError(null);
          }
        } catch (e) {
          logError('voice_profile_background_refresh_failed', {
            message: e instanceof Error ? e.message : String(e),
          });
        }
      })();
    }, 3000);

    return () => window.clearInterval(timer);
  }, [profiles, disabled, selectedProfileId, updateProfiles]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      if (testAudioUrl) URL.revokeObjectURL(testAudioUrl);
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, [previewUrl, testAudioUrl]);

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  };

  const clearRecordingUi = () => {
    setProfileName('');
    setRecordedBlob(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
  };

  const clearJsonUi = () => {
    setJsonDisplayName('');
    setJsonFile(null);
    if (jsonInputRef.current) jsonInputRef.current.value = '';
  };

  const startRecording = async () => {
    if (!supportsAudioClone) return;
    setError(null);
    chunksRef.current = [];
    clearRecordingUi();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };

      recorder.onstop = () => {
        stopStream();
        const blob = new Blob(chunksRef.current, { type: mimeType });
        setRecordedBlob(blob);
        setPreviewUrl(URL.createObjectURL(blob));
        setPhase('recorded');
        log('voice_recording_stopped', { bytes: blob.size, mimeType });
      };

      recorder.start(200);
      setPhase('recording');
      log('voice_recording_started', { mimeType });

      window.setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, DEFAULT_RECORD_SECONDS * 1000);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Microphone access denied';
      setError(msg);
      setPhase('idle');
      logError('voice_recording_failed', { message: msg });
    }
  };

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state === 'recording') recorder.stop();
  };

  const handleCreateProfile = async () => {
    if (!consentConfirmed) {
      setError('Confirm you have permission to use this voice.');
      return;
    }
    if (!recordedBlob) {
      setError('Record a voice sample first.');
      return;
    }
    const name = profileName.trim();
    if (!name) {
      setError('Enter a name for this voice profile.');
      return;
    }

    setPhase('uploading');
    setError(null);
    try {
      const ext = recordedBlob.type.includes('webm') ? 'webm' : 'wav';
      const profile = await cloneVoiceProfile(
        name,
        recordedBlob,
        `recording.${ext}`,
        consentConfirmed,
      );
      onSelectProfile(profile.id);
      await refreshProfiles();
      clearRecordingUi();
      setPhase('idle');
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Upload failed';
      logError('VOICE_PROFILE_UPLOAD_FAILED', { method: 'audio_clone', message: msg });
      setError(`Voice profile upload failed: ${msg}`);
      setPhase('failed');
      await refreshProfiles();
    }
  };

  const handleJsonUpload = async () => {
    if (!consentConfirmed) {
      setError('Confirm you have permission to use this voice.');
      return;
    }
    if (!jsonFile) {
      setError('Select a Voice Builder JSON file.');
      return;
    }
    const name = jsonDisplayName.trim();
    if (!name) {
      setError('Enter a display name for this voice profile.');
      return;
    }

    setPhase('uploading');
    setError(null);
    try {
      const profile = await uploadJsonVoiceProfile(jsonFile, name, consentConfirmed);
      onSelectProfile(profile.id);
      await refreshProfiles();
      clearJsonUi();
      setPhase('idle');
      setError(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'JSON upload failed';
      logError('VOICE_PROFILE_UPLOAD_FAILED', { method: 'voice_builder_json', message: msg });
      setError(`Voice profile upload failed: ${msg}`);
      setPhase('failed');
      await refreshProfiles();
    }
  };

  const handleTestVoice = async () => {
    if (!selectedProfileId) return;
    const profile = profiles.find((p) => p.id === selectedProfileId);
    if (!profile || !isVoiceProfileUsable(profile)) return;

    setPhase('testing');
    setError(null);
    try {
      const blob = await testVoiceProfile(selectedProfileId);
      if (testAudioUrl) URL.revokeObjectURL(testAudioUrl);
      const url = URL.createObjectURL(blob);
      setTestAudioUrl(url);
      setPhase('idle');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Voice test failed');
      setPhase('failed');
      await refreshProfiles();
    }
  };

  const handleRefreshSelected = async () => {
    if (!selectedProfileId) return;
    setError(null);
    setPhase('processing');
    try {
      const profile = await refreshVoiceProfile(selectedProfileId, { wait: true });
      const merged = profiles.map((p) => (p.id === profile.id ? profile : p));
      updateProfiles(merged);
      if (isVoiceProfileUsable(profile)) {
        setPhase('idle');
      } else {
        setError(
          profile.error_message ||
            `Still processing on RunPod (status: ${profile.status}).`,
        );
        setPhase('processing');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Refresh failed');
      setPhase('failed');
    }
  };

  const handleDelete = async () => {
    if (!selectedProfileId) return;
    const profile = profiles.find((p) => p.id === selectedProfileId);
    if (!profile) return;
    if (!window.confirm(`Delete voice profile "${profile.name}"?`)) return;

    setError(null);
    try {
      await deleteVoiceProfile(selectedProfileId);
      onSelectProfile(null);
      await refreshProfiles();
      setPhase('idle');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const readyProfiles = profiles.filter(isVoiceProfileUsable);
  const processingProfiles = profiles.filter((p) => p.status === 'processing');
  const selectedProfile = profiles.find((p) => p.id === selectedProfileId) ?? null;
  const busy =
    phase === 'uploading' || phase === 'processing' || phase === 'testing';

  if (loading && !capabilitiesLoaded) {
    return (
      <p className="voice-profile-hint" role="status">
        Loading voice profile capabilities…
      </p>
    );
  }

  if (capabilitiesLoaded && !cloningEnabled) {
    return (
      <p className="voice-profile-hint" role="status">
        {capabilitiesMessage ||
          'Custom voice profiles are disabled on this server. Use preset M1/F1.'}
      </p>
    );
  }

  return (
    <div className="voice-profile-panel">
      <label className="voice-select-label" htmlFor="voice-profile-select">
        Saved voice profiles
      </label>
      <select
        id="voice-profile-select"
        className="voice-select voice-select--wide"
        value={selectedProfileId ?? ''}
        onChange={(e) => {
          const nextId = e.target.value || null;
          onSelectProfile(nextId);
          const profile = nextId ? profiles.find((p) => p.id === nextId) : null;
          log('VOICE_PROFILE_SELECTED', {
            voice_profile_id: nextId,
            name: profile?.name ?? null,
            status: profile?.status ?? null,
            provider_voice_id: profile?.provider_voice_id ?? null,
            usable: profile ? isVoiceProfileUsable(profile) : false,
          });
        }}
        disabled={disabled || loading}
        aria-label="Select cloned voice profile"
      >
        <option value="">
          {loading
            ? 'Loading…'
            : readyProfiles.length
              ? 'Select a profile…'
              : processingProfiles.length
                ? 'Processing on RunPod…'
                : 'No profiles yet'}
        </option>
        {readyProfiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
            {p.is_default ? ' (default)' : ''}
          </option>
        ))}
        {processingProfiles.map((p) => (
          <option key={p.id} value={p.id} disabled>
            {p.name} (processing…)
          </option>
        ))}
      </select>

      {selectedProfile && !isVoiceProfileUsable(selectedProfile) && (
        <p className="voice-profile-warning" role="status">
          {selectedProfile.status === 'processing'
            ? 'RunPod is building your voice profile. This usually takes a few seconds.'
            : `Profile is ${selectedProfile.status}. Choose a ready profile or preset voice.`}
        </p>
      )}

      {selectedProfile && isVoiceProfileUsable(selectedProfile) && (
        <div className="voice-profile-preview">
          <button
            type="button"
            className="voice-profile-btn"
            onClick={() => void handleTestVoice()}
            disabled={disabled || busy}
          >
            Test voice
          </button>
          {testAudioUrl && (
            <audio controls src={testAudioUrl} className="voice-profile-audio" />
          )}
        </div>
      )}

      {supportsJsonUpload && (
        <div className="voice-profile-json-section">
          <p className="voice-profile-hint" role="status">
            Upload a Voice Builder JSON from{' '}
            <a
              href="https://supertonic.supertone.ai/voice-builder"
              target="_blank"
              rel="noreferrer"
            >
              Supertone Voice Builder
            </a>
            .
          </p>
          <div className="voice-profile-name-row">
            <input
              ref={jsonInputRef}
              type="file"
              accept=".json,application/json"
              className="voice-profile-file"
              disabled={disabled || busy}
              onChange={(e) => setJsonFile(e.target.files?.[0] ?? null)}
              aria-label="Voice Builder JSON file"
            />
            <input
              type="text"
              className="voice-profile-input"
              placeholder="Display name"
              value={jsonDisplayName}
              onChange={(e) => setJsonDisplayName(e.target.value)}
              disabled={disabled || busy}
              maxLength={128}
              aria-label="Voice profile display name"
            />
            <button
              type="button"
              className="voice-profile-btn voice-profile-btn--primary"
              onClick={() => void handleJsonUpload()}
              disabled={
                disabled || busy || !jsonFile || !jsonDisplayName.trim() || !consentConfirmed
              }
            >
              Upload JSON
            </button>
          </div>
        </div>
      )}

      {!supportsAudioClone && (
        <p className="voice-profile-hint" role="status">
          {capabilitiesMessage ||
            'Recording-based cloning is not enabled on this server. Upload a Voice Builder JSON file instead.'}
        </p>
      )}

      {supportsAudioClone && (
        <>
          <div className="voice-profile-record-row">
            {phase !== 'recording' ? (
              <button
                type="button"
                className="voice-profile-btn"
                onClick={() => void startRecording()}
                disabled={disabled || busy}
              >
                Record new voice
              </button>
            ) : (
              <button
                type="button"
                className="voice-profile-btn voice-profile-btn--stop"
                onClick={stopRecording}
              >
                Stop recording
              </button>
            )}
            {phase === 'recording' && (
              <span className="voice-profile-recording" aria-live="polite">
                Recording… (up to {DEFAULT_RECORD_SECONDS}s)
              </span>
            )}
          </div>

          {previewUrl && (
            <div className="voice-profile-preview">
              <span className="voice-select-label">Recording preview</span>
              <audio controls src={previewUrl} className="voice-profile-audio" />
            </div>
          )}

          <div className="voice-profile-name-row">
            <input
              type="text"
              className="voice-profile-input"
              placeholder="Voice profile name"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              disabled={disabled || busy}
              maxLength={128}
              aria-label="Voice profile name"
            />
            <button
              type="button"
              className="voice-profile-btn voice-profile-btn--primary"
              onClick={() => void handleCreateProfile()}
              disabled={
                disabled ||
                !recordedBlob ||
                busy ||
                !profileName.trim() ||
                !consentConfirmed
              }
            >
              Create voice profile
            </button>
          </div>
        </>
      )}

      <label className="voice-profile-consent">
        <input
          type="checkbox"
          checked={consentConfirmed}
          onChange={(e) => setConsentConfirmed(e.target.checked)}
          disabled={disabled || busy}
        />
        I confirm I have permission to use this voice.
      </label>

      {phase === 'uploading' && (
        <span className="voice-profile-recording" aria-live="polite">
          Uploading to backend…
        </span>
      )}
      {phase === 'processing' && (
        <span className="voice-profile-recording" aria-live="polite">
          Processing on RunPod…
        </span>
      )}
      {phase === 'testing' && (
        <span className="voice-profile-recording" aria-live="polite">
          Testing voice…
        </span>
      )}

      {selectedProfileId && (
        <div className="voice-profile-record-row">
          <button
            type="button"
            className="voice-profile-btn"
            onClick={() => void handleRefreshSelected()}
            disabled={disabled || busy}
          >
            Refresh status
          </button>
          <button
            type="button"
            className="voice-profile-btn voice-profile-btn--danger"
            onClick={() => void handleDelete()}
            disabled={disabled || phase === 'uploading'}
          >
            Delete profile
          </button>
        </div>
      )}

      {error && (
        <p className="voice-profile-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
