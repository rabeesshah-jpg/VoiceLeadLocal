import { IconPhone } from './icons';
import type { CallLanguage } from '../lib/callLanguage';
import { CALL_LANGUAGES } from '../lib/callLanguage';
import type { VoiceGender } from '../lib/voicePresets';
import { VOICE_PRESETS } from '../lib/voicePresets';
import VoiceProfilePanel from './VoiceProfilePanel';
import type { VoiceProfile } from '../api/voiceProfiles';

export type VoiceMode = 'preset' | 'custom';

interface Props {
  voiceMode: VoiceMode;
  onVoiceModeChange: (mode: VoiceMode) => void;
  voice: VoiceGender;
  onVoiceChange: (voice: VoiceGender) => void;
  voiceProfileId: string | null;
  onVoiceProfileChange: (id: string | null) => void;
  voiceProfiles: VoiceProfile[];
  onVoiceProfilesChange: (profiles: VoiceProfile[]) => void;
  language: CallLanguage;
  onLanguageChange: (language: CallLanguage) => void;
  inCall: boolean;
  busy: boolean;
  startDisabled?: boolean;
  startDisabledReason?: string | null;
  onCall: () => void;
}

export default function CallFooter({
  voiceMode,
  onVoiceModeChange,
  voice,
  onVoiceChange,
  voiceProfileId,
  onVoiceProfileChange,
  voiceProfiles,
  onVoiceProfilesChange,
  language,
  onLanguageChange,
  inCall,
  busy,
  startDisabled = false,
  startDisabledReason,
  onCall,
}: Props) {
  const selectedProfile = voiceProfiles.find((p) => p.id === voiceProfileId);
  const profileNotReady =
    voiceMode === 'custom' &&
    voiceProfileId &&
    selectedProfile &&
    (selectedProfile.status !== 'ready' || !selectedProfile.provider_voice_id?.trim());

  return (
    <div className="call-footer">
      <div className="voice-mode-section">
        <span className="voice-select-label">Voice mode</span>
        <div className="voice-mode-toggle" role="radiogroup" aria-label="Voice mode">
          <label className="voice-mode-option">
            <input
              type="radio"
              name="voice-mode"
              value="preset"
              checked={voiceMode === 'preset'}
              onChange={() => onVoiceModeChange('preset')}
              disabled={inCall || busy}
            />
            Preset voice
          </label>
          <label className="voice-mode-option">
            <input
              type="radio"
              name="voice-mode"
              value="custom"
              checked={voiceMode === 'custom'}
              onChange={() => onVoiceModeChange('custom')}
              disabled={inCall || busy}
            />
            Custom voice
          </label>
        </div>
      </div>

      {voiceMode === 'custom' && (
        <VoiceProfilePanel
          selectedProfileId={voiceProfileId}
          onSelectProfile={onVoiceProfileChange}
          disabled={inCall || busy}
          onProfilesChange={onVoiceProfilesChange}
        />
      )}

      <div className="call-footer-row">
        <div className="call-footer-cell call-footer-cell--start">
          <label className="voice-select-label" htmlFor="language-select">
            Language
          </label>
          <select
            id="language-select"
            className="voice-select"
            value={language}
            onChange={(e) => onLanguageChange(e.target.value as CallLanguage)}
            disabled={inCall || busy}
            aria-label="Select conversation language"
          >
            {CALL_LANGUAGES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {voiceMode === 'preset' && (
          <div className="call-footer-cell call-footer-cell--center">
            <label className="voice-select-label" htmlFor="voice-select">
              Voice
            </label>
            <select
              id="voice-select"
              className="voice-select"
              value={voice}
              onChange={(e) => onVoiceChange(e.target.value as VoiceGender)}
              disabled={inCall || busy}
              aria-label="Select assistant voice"
            >
              {(Object.keys(VOICE_PRESETS) as VoiceGender[]).map((key) => (
                <option key={key} value={key}>
                  {VOICE_PRESETS[key].label}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="call-footer-cell call-footer-cell--end">
          <button
            type="button"
            className={`call-btn ${inCall ? 'call-btn--active' : ''}`}
            onClick={onCall}
            disabled={busy || (!inCall && startDisabled)}
            aria-label={inCall ? 'End call' : 'Start voice call'}
            title={!inCall && startDisabled ? startDisabledReason ?? undefined : undefined}
          >
            <IconPhone />
            <span>{inCall ? 'End call' : 'Start call'}</span>
          </button>
        </div>
      </div>

      {!inCall && (startDisabledReason || profileNotReady) && (
        <p className="voice-profile-warning" role="status">
          {startDisabledReason ||
            'Selected voice profile is not ready. Wait or choose a preset voice.'}
        </p>
      )}

      {/* <p className="chat-powered-by">Powered by Agent Nora</p> */}
    </div>
  );
}