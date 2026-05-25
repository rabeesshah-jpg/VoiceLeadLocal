/** Helpers for committing STT/LLM lines without duplicating cumulative text. */

export function normalizeTranscriptText(s: string): string {
  return s.trim().replace(/\s+/g, ' ').toLowerCase();
}

/**
 * Return the new segment to append, or null if this final is a duplicate / empty.
 *
 * Handles:
 * - Independent utterances ("Hi" then "Weather?")
 * - Cumulative finals ("Hi" then "Hi Weather?")
 * - Exact duplicate re-fires
 */
export function extractNewTranscriptSegment(
  fullText: string,
  lastCommittedRaw: string,
): string | null {
  const full = fullText.trim();
  if (!full) return null;

  const last = lastCommittedRaw.trim();
  if (!last) return full;

  const normFull = normalizeTranscriptText(full);
  const normLast = normalizeTranscriptText(last);
  if (!normFull || normFull === normLast) return null;

  const lowerFull = full.toLowerCase();
  const lowerLast = last.toLowerCase();
  if (lowerFull.startsWith(lowerLast)) {
    const delta = full.slice(last.length).trim();
    return delta || null;
  }

  return full;
}

/** Raw text to store after committing (cumulative-aware). */
export function committedTranscriptRaw(
  fullText: string,
  lastCommittedRaw: string,
): string {
  const full = fullText.trim();
  const last = lastCommittedRaw.trim();
  if (!last) return full;
  const lowerFull = full.toLowerCase();
  const lowerLast = last.toLowerCase();
  if (lowerFull.startsWith(lowerLast)) return full;
  return `${last} ${full}`.trim();
}
