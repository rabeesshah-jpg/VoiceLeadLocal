/** Supertonic voice presets (male → M1, female → F1). */

export type VoiceGender = 'male' | 'female';

export interface TtsVoiceSettings {
  voice: string;
  lang: string;
}

export const VOICE_PRESETS: Record<VoiceGender, { label: string; tts: TtsVoiceSettings }> = {
  male: {
    label: 'Male (M1)',
    tts: {
      voice: 'M1',
      lang: 'en',
    },
  },
  female: {
    label: 'Female (F1)',
    tts: {
      voice: 'F1',
      lang: 'en',
    },
  },
};

export const DEFAULT_VOICE_GENDER: VoiceGender = 'male';

export function isVoiceGender(value: string): value is VoiceGender {
  return value === 'male' || value === 'female';
}
