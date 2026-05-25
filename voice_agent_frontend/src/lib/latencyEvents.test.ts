import { describe, expect, it } from 'vitest';
import {
  applyLlmMilestone,
  applyTurnSummaryLatency,
  getTelemetryEventKind,
  initialTurnLatencyMetrics,
  isLlmMilestoneEvent,
  startNewTurnMetrics,
} from './latencyEvents';

describe('latencyEvents', () => {
  it('reads type or event field', () => {
    expect(getTelemetryEventKind({ type: 'latency' })).toBe('latency');
    expect(getTelemetryEventKind({ event: 'llm_first_token' })).toBe('llm_first_token');
  });

  it('applies bundled latency summary from backend contract', () => {
    const active = startNewTurnMetrics(initialTurnLatencyMetrics);
    const result = applyTurnSummaryLatency(active, {
      type: 'latency',
      turn_id: 'abc123',
      llm_start_ms: 320,
      llm_first_token_ms: 420,
      tts_ttfb_ms: 760,
      tts_total_ms: 2100,
      tts_call_count: 3,
    });
    expect(result.turnId).toBe('abc123');
    expect(result.llmFirstTokenMs).toBe(320);
    expect(result.ttsFirstChunkMs).toBe(760);
    expect(result.ttsTotalMs).toBe(2100);
    expect(result.ttsCallCount).toBe(3);
    expect(result.llmStatus).toBe('received');
    expect(result.ttsStatus).toBe('received');
    expect(result.turnActive).toBe(false);
  });

  it('marks missing summary fields as not_received', () => {
    const active = startNewTurnMetrics(initialTurnLatencyMetrics);
    const result = applyTurnSummaryLatency(active, {
      type: 'latency',
      turn_id: 'x1',
    });
    expect(result.llmStatus).toBe('not_received');
    expect(result.ttsStatus).toBe('not_received');
  });

  it('applies llm ms from summary even when turn was idle', () => {
    const result = applyTurnSummaryLatency(initialTurnLatencyMetrics, {
      type: 'latency',
      turn_id: 'idle1',
      llm_start_ms: 280,
      tts_ttfb_ms: 195,
    });
    expect(result.llmFirstTokenMs).toBe(280);
    expect(result.llmStatus).toBe('received');
    expect(result.ttsFirstChunkMs).toBe(195);
  });

  it('handles live llm_start milestone event', () => {
    const active = startNewTurnMetrics(initialTurnLatencyMetrics);
    const result = applyLlmMilestone(active, {
      type: 'llm_start',
      turn_id: 'live1',
      llm_start_ms: 88,
    });
    expect(result.llmFirstTokenMs).toBe(88);
    expect(result.llmStatus).toBe('received');
  });

  it('detects llm milestone event aliases', () => {
    expect(isLlmMilestoneEvent('llm_first_token', {})).toBe(true);
    expect(isLlmMilestoneEvent('latency', {})).toBe(false);
  });
});
