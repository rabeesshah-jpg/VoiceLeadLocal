import { useCallback, useEffect, useRef, useState } from 'react';
import type { Room } from 'livekit-client';
import { RoomEvent } from 'livekit-client';
import { log } from '../lib/logger';
import {
  buildUserFinalDedupeKey,
  clearActiveUserTurn,
  createUserTranscriptSession,
  discardOpenUserDraft,
  finishAssistantStreaming,
  recordUserFinalCommit,
  removeIncompleteAssistant,
  shouldSkipUserFinalCommit,
  turnsToBubbles,
  upsertAssistantTranscript,
  upsertUserTranscript,
  type ChatTurn,
  type UserTranscriptSession,
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

function transcriptLog(step: string, detail?: Record<string, unknown>) {
  console.log('[VoiceAgent]', step, detail ?? '');
}

export function useAgentDataChannel() {
  const [state, setState] = useState<TranscriptState>(initial);

  const turnsRef = useRef<ChatTurn[]>([]);
  const userSessionRef = useRef<UserTranscriptSession>(createUserTranscriptSession());
  const seenUserFinalKeysRef = useRef<Set<string>>(new Set());
  const recentUserFinalAtRef = useRef<Map<string, number>>(new Map());
  const primaryAgentRef = useRef<string | null>(null);
  const boundRoomNameRef = useRef<string | null>(null);
  const dataHandlerRef = useRef<
    (payload: Uint8Array, identity: string) => void
  >(() => {});

  const reset = useCallback(() => {
    turnsRef.current = [];
    userSessionRef.current = createUserTranscriptSession();
    seenUserFinalKeysRef.current.clear();
    recentUserFinalAtRef.current.clear();
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
          log('ignore_duplicate_agent', {
            from: fromIdentity,
            primary: primaryAgentRef.current,
          });
          return;
        }
      }

      const kind = getTelemetryEventKind(msg);

      setState((prev) => {
        let turns = [...turnsRef.current];
        let userSession = userSessionRef.current;
        let patch: Partial<TranscriptState> = {};

        switch (kind) {
          case 'agent_state':
            if (msg.state) {
              const old = prev.agentState;
              patch.agentState = msg.state;

              if (msg.state === 'listening' && old === 'speaking') {
                turns = finishAssistantStreaming(turns);
              }

              if (msg.state === 'interrupted') {
                turns = removeIncompleteAssistant(turns);
                turns = finishAssistantStreaming(turns);
              } else if (msg.state === 'thinking' && old === 'speaking') {
                const last = turns.at(-1);
                if (!last?.assistantText.trim()) {
                  turns = removeIncompleteAssistant(turns);
                }
                turns = finishAssistantStreaming(turns);
              }

              if (
                msg.state === 'speaking' ||
                (msg.state === 'thinking' && old !== 'thinking')
              ) {
                turns = discardOpenUserDraft(turns);
                userSession = clearActiveUserTurn(userSession);
                transcriptLog('USER_DRAFT_DISCARDED', {
                  reason: 'agent_state',
                  state: msg.state,
                });
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
            if (!msg.text?.trim()) break;
            const isFinal = Boolean(msg.is_final);
            const text = msg.text;

            if (!isFinal) {
              const seq = msg.turn_seq ?? 0;
              const agentBusy =
                prev.agentState === 'speaking' ||
                prev.agentState === 'thinking';
              const isNewTurnAfterFinal =
                seq > 0 && seq > userSession.lastFinalizedTurnSeq;

              if (agentBusy && !isNewTurnAfterFinal) {
                transcriptLog('USER_PARTIAL_SUPPRESSED', {
                  reason: 'agent_busy',
                  agent_state: prev.agentState,
                  turn_seq: seq,
                  last_finalized_turn_seq: userSession.lastFinalizedTurnSeq,
                });
                break;
              }

              const result = upsertUserTranscript(
                turns,
                userSession,
                text,
                false,
                msg.turn_seq,
                msg.message_id,
              );
              turns = result.turns;
              userSession = result.session;

              if (result.action === 'DUPLICATE_TRANSCRIPT_DROPPED') {
                transcriptLog('DUPLICATE_TRANSCRIPT_DROPPED', {
                  phase: 'partial',
                  turn_seq: msg.turn_seq,
                });
                break;
              }

              transcriptLog(
                result.action === 'USER_MESSAGE_CREATED'
                  ? 'USER_MESSAGE_CREATED'
                  : 'TRANSCRIPT_PARTIAL_UPDATE',
                {
                  turn_seq: msg.turn_seq,
                  message_id: msg.message_id,
                  active_user_message_id: userSession.activeUserMessageId,
                  text_preview: text.slice(0, 80),
                },
              );
              if (result.action === 'USER_MESSAGE_UPDATED') {
                transcriptLog('USER_MESSAGE_UPDATED', {
                  turn_seq: msg.turn_seq,
                  text_preview: text.slice(0, 80),
                });
              }
              break;
            }

            const dedupeKey = buildUserFinalDedupeKey(
              text,
              msg.turn_seq,
              msg.message_id,
            );
            if (
              shouldSkipUserFinalCommit(
                seenUserFinalKeysRef.current,
                dedupeKey,
                recentUserFinalAtRef.current,
              )
            ) {
              transcriptLog('DUPLICATE_TRANSCRIPT_DROPPED', {
                phase: 'final',
                dedupe_key: dedupeKey,
                turn_seq: msg.turn_seq,
              });
              break;
            }

            const result = upsertUserTranscript(
              turns,
              userSession,
              text,
              true,
              msg.turn_seq,
              msg.message_id,
            );
            turns = result.turns;
            userSession = result.session;

            recordUserFinalCommit(
              seenUserFinalKeysRef.current,
              recentUserFinalAtRef.current,
              dedupeKey,
            );
            patch.agentState = 'thinking';
            patch.turnMetrics = startNewTurnMetrics(prev.turnMetrics);

            transcriptLog('TRANSCRIPT_FINAL_UPDATE', {
              turn_seq: msg.turn_seq,
              message_id: msg.message_id,
              text_preview: text.slice(0, 80),
            });
            transcriptLog('USER_MESSAGE_FINALIZED', {
              dedupe_key: dedupeKey,
              active_user_message_id: userSession.activeUserMessageId,
            });
            transcriptLog('ACTIVE_USER_TURN_CLEARED', {
              last_finalized_turn_seq: userSession.lastFinalizedTurnSeq,
            });
            break;
          }

          case 'llm_response':
          case 'agent_text': {
            if (!msg.text?.trim()) break;
            const streaming = kind === 'llm_response' ? !msg.is_final : true;
            const text = msg.text;

            const result = upsertAssistantTranscript(
              turns,
              userSession,
              text,
              streaming,
            );
            turns = result.turns;
            userSession = result.session;

            if (result.action === 'ASSISTANT_EVENT_DROPPED_REASON') {
              transcriptLog('ASSISTANT_EVENT_DROPPED_REASON', {
                reason: result.dropReason,
                kind,
              });
              break;
            }

            if (
              result.action === 'ASSISTANT_MESSAGE_CREATED' ||
              result.action === 'ASSISTANT_MESSAGE_UPDATED'
            ) {
              transcriptLog(
                result.action === 'ASSISTANT_MESSAGE_CREATED'
                  ? 'ASSISTANT_MESSAGE_CREATED'
                  : 'ASSISTANT_MESSAGE_UPDATED',
                { text_preview: text.slice(0, 80), streaming },
              );
            }
            if (result.action === 'ASSISTANT_MESSAGE_FINALIZED' || !streaming) {
              turns = finishAssistantStreaming(turns);
              transcriptLog('ASSISTANT_MESSAGE_FINALIZED', {
                text_preview: text.slice(0, 80),
              });
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
        userSessionRef.current = userSession;
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

  useEffect(() => {
    dataHandlerRef.current = handleMessage;
  }, [handleMessage]);

  const bindRoom = useCallback((room: Room) => {
    const roomName = room.name ?? '';
    log('data_channel_bind', { room: roomName });

    const onData = (
      payload: Uint8Array,
      participant?: { identity?: string },
    ) => {
      const identity = participant?.identity ?? '';
      if (identity.startsWith('user-')) {
        return;
      }
      dataHandlerRef.current(payload, identity);
    };

    room.on(RoomEvent.DataReceived, onData);
    boundRoomNameRef.current = roomName;

    return () => {
      room.off(RoomEvent.DataReceived, onData);
      if (boundRoomNameRef.current === roomName) {
        boundRoomNameRef.current = null;
      }
      log('data_channel_unbind', { room: roomName });
    };
  }, []);

  return { state, bindRoom, reset };
}
