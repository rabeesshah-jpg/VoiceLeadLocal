import { useCallback, useRef, useState } from 'react';
import type { Room } from 'livekit-client';
import { RoomEvent } from 'livekit-client';
import { log } from '../lib/logger';
import {
  applyLlmMilestone,
  applyTtsMilestone,
  applyTurnSummaryLatency,
  finalizeWaitingMilestones,
  getTelemetryEventKind,
  initialTurnLatencyMetrics,
  isLlmMilestoneEvent,
  isTtsMilestoneEvent,
  startNewTurnMetrics,
  type TurnLatencyMetrics,
} from '../lib/latencyEvents';

export type { MilestoneStatus, TurnLatencyMetrics } from '../lib/latencyEvents';

export type AgentUiState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'initializing';

export interface AgentDataMessage {
  type?: string;
  event?: string;
  state?: AgentUiState;
  text?: string;
  is_final?: boolean;
  code?: string;
  message?: string;
  stt_ms?: number;
  llm_first_token_ms?: number;
  tts_first_byte_ms?: number;
  tts_first_chunk_ms?: number;
  duration_ms?: number;
  ts?: number;
  turn_id?: string;
  component?: string;
  metric?: string;
}

export interface TranscriptState {
  userLines: string[];
  agentLines: string[];
  pendingUser: string;
  pendingAgent: string;
  agentState: AgentUiState;
  lastLatency: AgentDataMessage | null;
  lastError: string | null;
  turnMetrics: TurnLatencyMetrics;
}

const initial: TranscriptState = {
  userLines: [],
  agentLines: [],
  pendingUser: '',
  pendingAgent: '',
  agentState: 'idle',
  lastLatency: null,
  lastError: null,
  turnMetrics: initialTurnLatencyMetrics,
};

function normalizeText(s: string): string {
  return s.trim().replace(/\s+/g, ' ').toLowerCase();
}

export function useAgentDataChannel() {
  const [state, setState] = useState<TranscriptState>(initial);

  const lastUserFinalRef = useRef('');
  const lastAgentFinalRef = useRef('');
  const primaryAgentRef = useRef<string | null>(null);
  const boundRoomNameRef = useRef<string | null>(null);

  const reset = useCallback(() => {
    lastUserFinalRef.current = '';
    lastAgentFinalRef.current = '';
    primaryAgentRef.current = null;
    boundRoomNameRef.current = null;
    setState(initial);
  }, []);

  const handleMessage = useCallback((raw: Uint8Array, fromIdentity: string) => {
    try {
      const msg = JSON.parse(new TextDecoder().decode(raw)) as AgentDataMessage;

      if (fromIdentity.startsWith('agent-')) {
        if (!primaryAgentRef.current) {
          primaryAgentRef.current = fromIdentity;
          log('primary_agent', { identity: fromIdentity });
        } else if (fromIdentity !== primaryAgentRef.current) {
          log('ignore_duplicate_agent', { from: fromIdentity, primary: primaryAgentRef.current });
          return;
        }
      }

      const kind = getTelemetryEventKind(msg);

      setState((prev) => {
        const next = { ...prev };
        switch (kind) {
          case 'agent_state':
            if (msg.state) {
              next.agentState = msg.state;
              if (
                msg.state === 'listening' &&
                prev.turnMetrics.turnActive
              ) {
                next.turnMetrics = finalizeWaitingMilestones(prev.turnMetrics);
              }
            }
            break;
          case 'user_transcript':
            if (!msg.text) break;
            if (msg.is_final) {
              const norm = normalizeText(msg.text);
              if (norm && norm !== lastUserFinalRef.current) {
                lastUserFinalRef.current = norm;
                next.userLines = [...prev.userLines, msg.text.trim()];
                next.pendingUser = '';
                if (norm) {
                  next.turnMetrics = startNewTurnMetrics(prev.turnMetrics);
                }
              }
            } else {
              next.pendingUser = msg.text;
            }
            break;
          case 'agent_text':
            if (!msg.text) break;
            if (msg.is_final) {
              const norm = normalizeText(msg.text);
              if (norm && norm !== lastAgentFinalRef.current) {
                lastAgentFinalRef.current = norm;
                next.agentLines = [...prev.agentLines, msg.text.trim()];
                next.pendingAgent = '';
              }
            } else {
              next.pendingAgent = msg.text;
            }
            break;
          case 'latency':
          case 'voice_turn_latency_summary':
            next.lastLatency = msg;
            next.turnMetrics = applyTurnSummaryLatency(prev.turnMetrics, msg);
            break;
          case 'error':
            next.lastError = msg.message || msg.code || 'Unknown error';
            break;
          default:
            if (isLlmMilestoneEvent(kind, msg)) {
              next.turnMetrics = applyLlmMilestone(prev.turnMetrics, msg);
            } else if (isTtsMilestoneEvent(kind, msg)) {
              next.turnMetrics = applyTtsMilestone(prev.turnMetrics, msg);
            }
            break;
        }
        return next;
      });
    } catch {
      /* ignore malformed */
    }
  }, []);

  const bindRoom = useCallback(
    (room: Room) => {
      if (boundRoomNameRef.current === room.name) {
        return () => {};
      }
      boundRoomNameRef.current = room.name;
      log('data_channel_bind', { room: room.name });

      const onData = (
        payload: Uint8Array,
        participant?: { identity?: string },
      ) => {
        const identity = participant?.identity ?? '';
        if (identity.startsWith('user-')) {
          return;
        }
        handleMessage(payload, identity);
      };

      room.on(RoomEvent.DataReceived, onData);
      return () => {
        room.off(RoomEvent.DataReceived, onData);
        if (boundRoomNameRef.current === room.name) {
          boundRoomNameRef.current = null;
        }
      };
    },
    [handleMessage],
  );

  return { state, bindRoom, reset };
}
