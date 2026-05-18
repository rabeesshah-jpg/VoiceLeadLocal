import { useCallback, useRef, useState } from 'react';
import type { Room } from 'livekit-client';
import { RoomEvent } from 'livekit-client';
import { log } from '../lib/logger';

export type AgentUiState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'initializing';

export interface AgentDataMessage {
  type: string;
  state?: AgentUiState;
  text?: string;
  is_final?: boolean;
  code?: string;
  message?: string;
  stt_ms?: number;
  llm_first_token_ms?: number;
  tts_first_byte_ms?: number;
  turn_id?: string;
}

export interface TranscriptState {
  userLines: string[];
  agentLines: string[];
  pendingUser: string;
  pendingAgent: string;
  agentState: AgentUiState;
  lastLatency: AgentDataMessage | null;
  lastError: string | null;
}

const initial: TranscriptState = {
  userLines: [],
  agentLines: [],
  pendingUser: '',
  pendingAgent: '',
  agentState: 'idle',
  lastLatency: null,
  lastError: null,
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

      setState((prev) => {
        const next = { ...prev };
        switch (msg.type) {
          case 'agent_state':
            if (msg.state) {
              next.agentState = msg.state;
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
            next.lastLatency = msg;
            break;
          case 'error':
            next.lastError = msg.message || msg.code || 'Unknown error';
            break;
          default:
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
