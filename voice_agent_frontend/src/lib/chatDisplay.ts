/** Paralinguistic tags sent to TTS — hidden in the chat UI. */
const PARALINGUISTIC_TAG_PATTERN =
  /\[(?:laugh|chuckle|sigh|gasp|cough|clear throat|sniff|groan|shush)\]/gi;

/** Strip TTS emotion tags and normalize spaces for display only. */
export function formatChatDisplayText(text: string): string {
  return text
    .replace(PARALINGUISTIC_TAG_PATTERN, '')
    .replace(/\s+([,.!?;:])/g, '$1')
    .replace(/([,.!?;:])\s*/g, '$1 ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
