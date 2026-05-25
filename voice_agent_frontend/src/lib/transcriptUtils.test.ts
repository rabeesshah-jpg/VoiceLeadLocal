import { describe, expect, it } from 'vitest';
import {
  committedTranscriptRaw,
  extractNewTranscriptSegment,
} from './transcriptUtils';

describe('extractNewTranscriptSegment', () => {
  it('returns full text on first commit', () => {
    expect(extractNewTranscriptSegment('Hello there', '')).toBe('Hello there');
  });

  it('skips exact duplicate finals', () => {
    const first = 'Hey. How are you?';
    expect(extractNewTranscriptSegment(first, first)).toBeNull();
  });

  it('appends delta for cumulative STT finals', () => {
    const first = 'Hey. How are you?';
    const cumulative = 'Hey. How are you? What is the weather?';
    expect(extractNewTranscriptSegment(cumulative, first)).toBe('What is the weather?');
  });

  it('appends independent utterances', () => {
    const first = 'Hey. How are you?';
    expect(extractNewTranscriptSegment('Tell me a joke', first)).toBe('Tell me a joke');
  });
});

describe('committedTranscriptRaw', () => {
  it('tracks cumulative buffer', () => {
    const first = 'Hey';
    const cumulative = 'Hey there';
    expect(committedTranscriptRaw(cumulative, first)).toBe('Hey there');
  });
});
