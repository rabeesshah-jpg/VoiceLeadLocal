/** Telemetry fields that may appear on LiveKit data-channel JSON payloads. */
export interface TelemetryPayload {
  type?: string;
  event?: string;
  turn_id?: string;
  duration_ms?: number;
  ts?: number;
  stt_ms?: number;
  llm_first_token_ms?: number;
  tts_first_byte_ms?: number;
  tts_first_chunk_ms?: number;
  component?: string;
  metric?: string;
}

export type MilestoneStatus = 'idle' | 'waiting' | 'received' | 'not_received';

export interface TurnLatencyMetrics {
  turnId: string | null;
  turnActive: boolean;
  llmFirstTokenAt: number | null;
  llmFirstTokenMs: number | null;
  llmStatus: MilestoneStatus;
  ttsFirstChunkAt: number | null;
  ttsFirstChunkMs: number | null;
  ttsStatus: MilestoneStatus;
}

export const initialTurnLatencyMetrics: TurnLatencyMetrics = {
  turnId: null,
  turnActive: false,
  llmFirstTokenAt: null,
  llmFirstTokenMs: null,
  llmStatus: 'idle',
  ttsFirstChunkAt: null,
  ttsFirstChunkMs: null,
  ttsStatus: 'idle',
};

const LLM_MILESTONE_EVENTS = new Set([
  'llm_first_token',
  'llm_first_token_generated',
  'llm_token_first',
]);

const TTS_MILESTONE_EVENTS = new Set([
  'tts_first_chunk',
  'tts_first_chunk_generated',
  'tts_ttfb',
]);

export function getTelemetryEventKind(msg: TelemetryPayload): string {
  if (typeof msg.type === 'string' && msg.type.length > 0) return msg.type;
  if (typeof msg.event === 'string' && msg.event.length > 0) return msg.event;
  return '';
}

function readMs(...values: (number | undefined)[]): number | null {
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

function readTs(msg: TelemetryPayload): number {
  if (typeof msg.ts === 'number' && Number.isFinite(msg.ts)) return msg.ts;
  return Date.now();
}

export function isLlmMilestoneEvent(kind: string, msg: TelemetryPayload): boolean {
  if (LLM_MILESTONE_EVENTS.has(kind)) return true;
  if (kind !== 'component_latency') return false;
  const key = `${msg.component ?? ''}:${msg.metric ?? ''}`.toLowerCase();
  return key.includes('llm') && key.includes('token');
}

export function isTtsMilestoneEvent(kind: string, msg: TelemetryPayload): boolean {
  if (TTS_MILESTONE_EVENTS.has(kind)) return true;
  if (kind !== 'component_latency') return false;
  const key = `${msg.component ?? ''}:${msg.metric ?? ''}`.toLowerCase();
  return key.includes('tts') && (key.includes('chunk') || key.includes('ttfb') || key.includes('byte'));
}

export function startNewTurnMetrics(_prev?: TurnLatencyMetrics): TurnLatencyMetrics {
  return {
    turnId: null,
    turnActive: true,
    llmFirstTokenAt: null,
    llmFirstTokenMs: null,
    llmStatus: 'waiting',
    ttsFirstChunkAt: null,
    ttsFirstChunkMs: null,
    ttsStatus: 'waiting',
  };
}

export function applyLlmMilestone(
  metrics: TurnLatencyMetrics,
  msg: TelemetryPayload,
): TurnLatencyMetrics {
  const ms = readMs(msg.duration_ms, msg.llm_first_token_ms);
  if (ms === null) return metrics;
  return {
    ...metrics,
    turnId: msg.turn_id ?? metrics.turnId,
    llmFirstTokenMs: ms,
    llmFirstTokenAt: readTs(msg),
    llmStatus: 'received',
  };
}

export function applyTtsMilestone(
  metrics: TurnLatencyMetrics,
  msg: TelemetryPayload,
): TurnLatencyMetrics {
  const ms = readMs(msg.duration_ms, msg.tts_first_byte_ms, msg.tts_first_chunk_ms);
  if (ms === null) return metrics;
  return {
    ...metrics,
    turnId: msg.turn_id ?? metrics.turnId,
    ttsFirstChunkMs: ms,
    ttsFirstChunkAt: readTs(msg),
    ttsStatus: 'received',
  };
}

export function applyTurnSummaryLatency(
  metrics: TurnLatencyMetrics,
  msg: TelemetryPayload,
): TurnLatencyMetrics {
  const next: TurnLatencyMetrics = {
    ...metrics,
    turnId: msg.turn_id ?? metrics.turnId,
    turnActive: false,
  };

  const llmMs = readMs(msg.llm_first_token_ms);
  if (next.llmStatus === 'waiting') {
    if (llmMs !== null) {
      next.llmFirstTokenMs = llmMs;
      next.llmFirstTokenAt = readTs(msg);
      next.llmStatus = 'received';
    } else {
      next.llmStatus = 'not_received';
    }
  } else if (llmMs !== null && next.llmFirstTokenMs === null) {
    next.llmFirstTokenMs = llmMs;
    next.llmFirstTokenAt = readTs(msg);
    next.llmStatus = 'received';
  }

  const ttsMs = readMs(msg.tts_first_byte_ms, msg.tts_first_chunk_ms);
  if (next.ttsStatus === 'waiting') {
    if (ttsMs !== null) {
      next.ttsFirstChunkMs = ttsMs;
      next.ttsFirstChunkAt = readTs(msg);
      next.ttsStatus = 'received';
    } else {
      next.ttsStatus = 'not_received';
    }
  } else if (ttsMs !== null && next.ttsFirstChunkMs === null) {
    next.ttsFirstChunkMs = ttsMs;
    next.ttsFirstChunkAt = readTs(msg);
    next.ttsStatus = 'received';
  }

  return next;
}

/** Mark any still-waiting milestones as not_received when a turn ends without data. */
export function finalizeWaitingMilestones(metrics: TurnLatencyMetrics): TurnLatencyMetrics {
  if (!metrics.turnActive) return metrics;
  return {
    ...metrics,
    turnActive: false,
    llmStatus: metrics.llmStatus === 'waiting' ? 'not_received' : metrics.llmStatus,
    ttsStatus: metrics.ttsStatus === 'waiting' ? 'not_received' : metrics.ttsStatus,
  };
}
