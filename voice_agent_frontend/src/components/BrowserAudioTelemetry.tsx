import { useEffect, useRef } from 'react';
import { useRoomContext } from '@livekit/components-react';
import {
  RoomEvent,
  Track,
  TrackEvent,
  type Participant,
  type RemoteAudioTrack,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client';
import { logUiTelemetry } from '../lib/uiTelemetry';

function isAgentParticipant(identity: string): boolean {
  return identity.startsWith('agent-') || identity.includes('voice-agent');
}

/** Logs when agent audio is received and when browser playback starts. */
export default function BrowserAudioTelemetry() {
  const room = useRoomContext();
  const receivedRef = useRef(new Set<string>());
  const playbackRef = useRef(new Set<string>());
  const cleanupRef = useRef<Array<() => void>>([]);

  useEffect(() => {
    if (!room) return;

    const cleanups: Array<() => void> = [];

    const onTrackSubscribed = (
      track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: Participant,
    ) => {
      if (track.kind !== Track.Kind.Audio || !isAgentParticipant(participant.identity)) {
        return;
      }

      const trackKey = `${participant.identity}:${publication.trackSid}`;
      if (!receivedRef.current.has(trackKey)) {
        receivedRef.current.add(trackKey);
        logUiTelemetry('browser_audio_received', {
          participant: participant.identity,
          track_sid: publication.trackSid,
          source: publication.source,
          room: room.name,
        });
      }

      const audioTrack = track as RemoteAudioTrack;
      const onPlaybackStarted = () => {
        const playbackKey = `${trackKey}:playing`;
        if (playbackRef.current.has(playbackKey)) return;
        playbackRef.current.add(playbackKey);
        logUiTelemetry('browser_audio_playback_started', {
          participant: participant.identity,
          track_sid: publication.trackSid,
          room: room.name,
        });
      };

      const onPlaybackFailed = (reason?: Error) => {
        logUiTelemetry('browser_audio_playback_failed', {
          participant: participant.identity,
          track_sid: publication.trackSid,
          room: room.name,
          error: reason?.message ?? 'unknown',
        });
      };

      audioTrack.on(TrackEvent.AudioPlaybackStarted, onPlaybackStarted);
      audioTrack.on(TrackEvent.AudioPlaybackFailed, onPlaybackFailed);
      cleanups.push(() => {
        audioTrack.off(TrackEvent.AudioPlaybackStarted, onPlaybackStarted);
        audioTrack.off(TrackEvent.AudioPlaybackFailed, onPlaybackFailed);
      });
    };

    const onTrackUnsubscribed = (
      _track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: Participant,
    ) => {
      const trackKey = `${participant.identity}:${publication.trackSid}`;
      receivedRef.current.delete(trackKey);
      playbackRef.current.delete(`${trackKey}:playing`);
    };

    const onAudioPlaybackStatusChanged = () => {
      logUiTelemetry('browser_audio_playback_status', {
        room: room.name,
        can_playback_audio: room.canPlaybackAudio,
      });
    };

    room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
    room.on(RoomEvent.AudioPlaybackStatusChanged, onAudioPlaybackStatusChanged);
    cleanups.push(() => {
      room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
      room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
      room.off(RoomEvent.AudioPlaybackStatusChanged, onAudioPlaybackStatusChanged);
    });

    cleanupRef.current = cleanups;
    return () => {
      cleanups.forEach((fn) => fn());
      cleanupRef.current = [];
    };
  }, [room]);

  return null;
}
