import { useEffect } from 'react';
import { useRoomContext } from '@livekit/components-react';

interface Props {
  muted: boolean;
}

export default function MuteSync({ muted }: Props) {
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;
    room.localParticipant.setMicrophoneEnabled(!muted).catch(() => {});
  }, [room, muted]);

  return null;
}
