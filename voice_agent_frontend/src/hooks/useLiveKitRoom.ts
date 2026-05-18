import { useCallback, useRef, useState } from 'react';
import { ConnectionState, Room, RoomEvent } from 'livekit-client';
import type { StartCallResponse } from '../api/calls';

export type ConnectionBadgeState =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'error';

export function useLiveKitRoom() {
  const roomRef = useRef<Room | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionBadgeState>('idle');
  const [error, setError] = useState<string | null>(null);
  const reconnectAttempted = useRef(false);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    roomRef.current = null;
    reconnectAttempted.current = false;
    if (room) {
      await room.disconnect();
    }
    setConnectionState('idle');
  }, []);

  const connect = useCallback(
    async (session: StartCallResponse, onRoom?: (room: Room) => void) => {
      setError(null);
      setConnectionState('connecting');
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        if (state === ConnectionState.Connected) {
          setConnectionState('live');
          reconnectAttempted.current = false;
        } else if (state === ConnectionState.Reconnecting) {
          setConnectionState('reconnecting');
        } else if (state === ConnectionState.Disconnected) {
          if (!reconnectAttempted.current && roomRef.current) {
            reconnectAttempted.current = true;
            setConnectionState('reconnecting');
            room
              .connect(session.livekit_url, session.participant_token)
              .catch(() => {
                setConnectionState('error');
                setError('Connection lost. Start a new call.');
              });
          } else {
            setConnectionState('error');
          }
        }
      });

      try {
        await room.connect(session.livekit_url, session.participant_token);
        await room.localParticipant.setMicrophoneEnabled(true);
        onRoom?.(room);
        setConnectionState('live');
      } catch (e) {
        setConnectionState('error');
        setError(e instanceof Error ? e.message : 'Failed to connect');
        throw e;
      }
    },
    [],
  );

  const setMuted = useCallback(async (muted: boolean) => {
    const room = roomRef.current;
    if (!room) return;
    await room.localParticipant.setMicrophoneEnabled(!muted);
  }, []);

  return {
    roomRef,
    connectionState,
    error,
    connect,
    disconnect,
    setMuted,
  };
}
