import { useEffect } from 'react';
import { useRoomContext } from '@livekit/components-react';
import type { Room } from 'livekit-client';

interface Props {
  bindRoom: (room: Room) => () => void;
}

/** Subscribes to agent data messages once inside LiveKitRoom context. */
export default function RoomDataBinder({ bindRoom }: Props) {
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;
    return bindRoom(room);
  }, [room, bindRoom]);

  return null;
}
