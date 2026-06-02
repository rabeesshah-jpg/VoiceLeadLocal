import { useEffect, useRef } from 'react';
import { useRoomContext } from '@livekit/components-react';
import type { Room } from 'livekit-client';

interface Props {
  bindRoom: (room: Room) => () => void;
}

/** Subscribes to agent data messages once inside LiveKitRoom context. */
export default function RoomDataBinder({ bindRoom }: Props) {
  const room = useRoomContext();
  const bindRoomRef = useRef(bindRoom);
  bindRoomRef.current = bindRoom;

  useEffect(() => {
    if (!room) return;
    return bindRoomRef.current(room);
  }, [room]);

  return null;
}
