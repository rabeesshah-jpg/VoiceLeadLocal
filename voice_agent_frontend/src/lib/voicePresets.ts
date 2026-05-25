/** Chatterbox voice presets sent with POST /api/calls/start/ */

export type VoiceGender = 'male' | 'female';

export interface TtsVoiceSettings {
  predefined_voice_id: string;
  exaggeration: number;
  temperature: number;
  cfg_weight: number;
}

export const VOICE_PRESETS: Record<VoiceGender, { label: string; tts: TtsVoiceSettings }> = {
  male: {
    label: 'Male (Michael)',
    tts: {
      predefined_voice_id: 'Michael.wav',
      exaggeration: 0.65,
      temperature: 0.8,
      cfg_weight: 0.45,
    },
  },
  female: {
    label: 'Female (Olivia)',
    tts: {
      predefined_voice_id: 'Olivia.wav',
      exaggeration: 0.65,
      temperature: 0.8,
      cfg_weight: 0.45,
    },
  },
};

export const DEFAULT_VOICE_GENDER: VoiceGender = 'female';

export function isVoiceGender(value: string): value is VoiceGender {
  return value === 'male' || value === 'female';
}
