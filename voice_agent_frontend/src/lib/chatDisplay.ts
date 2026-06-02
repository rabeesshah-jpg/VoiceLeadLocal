/** Supertonic expression tags — hidden in the chat UI. */
const SUPERTONIC_TAG_PATTERN = /<(?:laugh|breath|sigh)>/gi;

/** Chatterbox paralinguistic tags (deprecated) */
const CHATTERBOX_TAG_PATTERN =
  /\[(?:laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan|shush)\]/gi;

/** STT / WLK bracket artifacts (not spoken words). */
const BRACKET_ARTIFACT_PATTERN =
  /\[(?:BLANK[_ ]?AUDIO|NOISE|MUSIC|SILENCE|INAUDIBLE|STATIC|UNINTELLIGIBLE)\]/gi;

/** Parenthetical non-speech cues from STT. */
const PAREN_ARTIFACT_PATTERN =
  /\s*\((?:laughs?|laughter|chuckle|chuckles|sighs?|gasp|cough|clears?\s*throat|sniff|groan|shush|music|noise|inaudible|silence|blank[_ ]?audio)\)\s*/gi;

const HALLUCINATION_PHRASE_PATTERNS = [
  /\bthanks?\s+for\s+watching[.!?,]?\s*/gi,
  /\bthank\s+you\s+for\s+watching[.!?,]?\s*/gi,
  /\bplease\s+subscribe[.!?,]?\s*/gi,
];

/** Trailing STT junk fragments (e.g. "... in this? No." or "Dubai. - Yeah."). */
const TRAILING_FILLER_PATTERN =
  /\s*[-–—]?\s*(?:yeah|yes|no|ok|okay|um|uh|hmm|right)\s*[.!?]?\s*$/i;

function stripTagsAndNormalize(text: string): string {
  return text
    .replace(SUPERTONIC_TAG_PATTERN, '')
    .replace(CHATTERBOX_TAG_PATTERN, '')
    .replace(/\s+([,.!?;:])/g, '$1')
    .replace(/([,.!?;:])\s*/g, '$1 ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/**
 * Remove STT artifacts and hallucinated phrases the user did not say.
 * Used for user transcript display and before committing user bubble text.
 */
export function sanitizeUserSpokenText(text: string): string {
  let t = (text || '').trim();
  if (!t) return '';

  t = t.replace(BRACKET_ARTIFACT_PATTERN, ' ');
  t = t.replace(PAREN_ARTIFACT_PATTERN, ' ');
  for (const pat of HALLUCINATION_PHRASE_PATTERNS) {
    t = t.replace(pat, ' ');
  }
  t = t.replace(TRAILING_FILLER_PATTERN, '');
  t = t.replace(/\s+[-–—]\s*$/g, '');
  return stripTagsAndNormalize(t);
}

/** Strip TTS emotion tags and normalize spaces for display only. */
export function formatChatDisplayText(text: string): string {
  return stripTagsAndNormalize(text);
}

/** User transcript bubbles — hide STT noise tags and common hallucinations. */
export function formatUserChatDisplayText(text: string): string {
  return sanitizeUserSpokenText(text);
}
