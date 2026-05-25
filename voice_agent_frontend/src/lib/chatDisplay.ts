/** Supertonic expression tags — hidden in the chat UI. */
const SUPERTONIC_TAG_PATTERN = /<(?:laugh|breath|sigh)>/gi;

/** Chatterbox paralinguistic tags (deprecated) */
const CHATTERBOX_TAG_PATTERN =
  /\[(?:laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan|shush)\]/gi;

function stripTagsAndNormalize(text: string): string {
  return text
    .replace(SUPERTONIC_TAG_PATTERN, '')
    .replace(CHATTERBOX_TAG_PATTERN, '')
    .replace(/\s+([,.!?;:])/g, '$1')
    .replace(/([,.!?;:])\s*/g, '$1 ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/** Strip TTS emotion tags and normalize spaces for display only. */
export function formatChatDisplayText(text: string): string {
  return stripTagsAndNormalize(text);
}

/** User bubbles are published in English by the agent worker. */
export function formatUserChatDisplayText(text: string): string {
  return stripTagsAndNormalize(text);
}
