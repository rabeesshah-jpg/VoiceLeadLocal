import { IconPhone } from './icons';
import type { CallLanguage } from '../lib/callLanguage';
import { CALL_LANGUAGES } from '../lib/callLanguage';
import type { VoiceGender } from '../lib/voicePresets';
import { VOICE_PRESETS } from '../lib/voicePresets';

interface Props {
  voice: VoiceGender;
  onVoiceChange: (voice: VoiceGender) => void;
  language: CallLanguage;
  onLanguageChange: (language: CallLanguage) => void;
  inCall: boolean;
  busy: boolean;
  onCall: () => void;
}

export default function CallFooter({
  voice,
  onVoiceChange,
  language,
  onLanguageChange,
  inCall,
  busy,
  onCall,
}: Props) {
  return (
    <div className="call-footer">
      <div className="call-footer-controls">
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

        <button
          type="button"
          className={`call-btn ${inCall ? 'call-btn--active' : ''}`}
          onClick={onCall}
          disabled={busy}
          aria-label={inCall ? 'End call' : 'Start voice call'}
        >
          <IconPhone />
          <span>{inCall ? 'End call' : 'Start call'}</span>
        </button>
      </div>
      <p className="chat-powered-by">Powered by Voice Agent</p>
    </div>
  );
}
