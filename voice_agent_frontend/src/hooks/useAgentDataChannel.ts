import { useCallback, useRef, useState } from 'react';
import type { Room } from 'livekit-client';
import { RoomEvent } from 'livekit-client';
import { log } from '../lib/logger';
import {
  applyAssistantTranscript,
  applyUserTranscript,
  finishAssistantStreaming,
  removeIncompleteAssistant,
  turnsToBubbles,
  type ChatTurn,
} from '../lib/chatTurns';
import type { ChatBubble } from '../lib/chatTurns';
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
export type { ChatBubble };

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
  interrupted?: boolean;
  message_id?: string;
  turn_seq?: number;
  code?: string;
  message?: string;
  stt_ms?: number;
  llm_start_ms?: number;
  llm_first_token_ms?: number;
  tts_ttfb_ms?: number;
  tts_first_byte_ms?: number;
  tts_first_chunk_ms?: number;
  tts_total_ms?: number;
  tts_call_count?: number;
  duration_ms?: number;
  ts?: number;
  turn_id?: string;
  component?: string;
  metric?: string;
}

export interface TranscriptState {
  /** Alternating user / assistant bubbles derived from turns. */
  bubbles: ChatBubble[];
  agentState: AgentUiState;
  lastLatency: AgentDataMessage | null;
  lastError: string | null;
  turnMetrics: TurnLatencyMetrics;
}

const initial: TranscriptState = {
  bubbles: [],
  agentState: 'idle',
  lastLatency: null,
  lastError: null,
  turnMetrics: initialTurnLatencyMetrics,
};

export function useAgentDataChannel() {
  const [state, setState] = useState<TranscriptState>(initial);

  const turnsRef = useRef<ChatTurn[]>([]);
  const seenUserMessageIdsRef = useRef<Set<string>>(new Set());
  const primaryAgentRef = useRef<string | null>(null);
  const boundRoomNameRef = useRef<string | null>(null);

  const reset = useCallback(() => {
    turnsRef.current = [];
    seenUserMessageIdsRef.current.clear();
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
        let turns = [...turnsRef.current];
        let patch: Partial<TranscriptState> = {};

        switch (kind) {
          case 'agent_state':
            if (msg.state) {
              const old = prev.agentState;
              patch.agentState = msg.state;

              if (msg.state === 'listening' && old === 'speaking') {
                turns = finishAssistantStreaming(turns);
              }

              if (
                msg.state === 'interrupted' ||
                (msg.state === 'thinking' && old === 'speaking')
              ) {
                turns = removeIncompleteAssistant(turns);
                turns = finishAssistantStreaming(turns);
              }

              if (msg.state === 'listening' && prev.turnMetrics.turnActive) {
                patch.turnMetrics = finalizeWaitingMilestones(prev.turnMetrics);
              }
            }
            break;

          case 'llm_playback_end':
            if (msg.interrupted) {
              turns = removeIncompleteAssistant(turns);
            } else {
              turns = finishAssistantStreaming(turns);
            }
            break;

          case 'user_transcript': {
            if (!msg.text) break;
            const isFinal = Boolean(msg.is_final);
            log('transcript_event', {
              role: 'user',
              is_final: isFinal,
              message_id: msg.message_id,
              turn_seq: msg.turn_seq,
              text_preview: msg.text.slice(0, 80),
            });

            const dedupeKey =
              msg.message_id ||
              (isFinal && msg.turn_seq
                ? `final:${msg.turn_seq}:${msg.text.trim()}`
                : '');

            if (isFinal && dedupeKey && seenUserMessageIdsRef.current.has(dedupeKey)) {
              log('transcript_skip_duplicate_id', { message_id: dedupeKey });
              break;
            }

            turns = applyUserTranscript(
              turns,
              msg.text,
              isFinal,
              msg.turn_seq,
            );

            if (isFinal) {
              if (dedupeKey) {
                seenUserMessageIdsRef.current.add(dedupeKey);
              }
              patch.turnMetrics = startNewTurnMetrics(prev.turnMetrics);
              log('transcript_append_user', {
                message_id: msg.message_id,
                turn_seq: msg.turn_seq,
                text_preview: msg.text.slice(0, 80),
                turn_count: turns.length,
              });
            }
            break;
          }

          case 'llm_response':
          case 'agent_text': {
            if (!msg.text?.trim()) break;
            const streaming = kind === 'llm_response' ? !msg.is_final : true;
            log('llm_response_event', {
              type: kind,
              is_final: msg.is_final,
              text_preview: msg.text.slice(0, 80),
            });
            const lastTurn = turns.at(-1);
            if (
              lastTurn?.userFinal &&
              lastTurn.assistantText.trim() === msg.text.trim()
            ) {
              if (!streaming) {
                turns = finishAssistantStreaming(turns);
              }
            } else {
              turns = applyAssistantTranscript(turns, msg.text, streaming);
            }
            break;
          }

          case 'llm_start':
          case 'llm_first_token':
            patch.turnMetrics = applyLlmMilestone(prev.turnMetrics, msg);
            break;

          case 'latency':
          case 'voice_turn_latency_summary':
            patch.lastLatency = msg;
            patch.turnMetrics = applyTurnSummaryLatency(prev.turnMetrics, msg);
            break;

          case 'error':
            patch.lastError = msg.message || msg.code || 'Unknown error';
            break;

          default:
            if (isLlmMilestoneEvent(kind, msg)) {
              patch.turnMetrics = applyLlmMilestone(prev.turnMetrics, msg);
            } else if (isTtsMilestoneEvent(kind, msg)) {
              patch.turnMetrics = applyTtsMilestone(prev.turnMetrics, msg);
            }
            break;
        }

        turnsRef.current = turns;
        return {
          ...prev,
          ...patch,
          bubbles: turnsToBubbles(turns),
        };
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
