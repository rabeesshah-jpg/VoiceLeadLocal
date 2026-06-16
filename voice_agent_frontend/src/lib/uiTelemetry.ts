/** Browser UI telemetry — console + Django API log file for terminal tail. */

import { postUiTelemetry } from '../api/uiTelemetry';

const PREFIX = '[VoiceAgent]';

type TelemetryContext = {
  callId: string | null;
  roomName: string | null;
};

const context: TelemetryContext = {
  callId: null,
  roomName: null,
};

export function setUiTelemetryCallContext(
  callId: string | null,
  roomName: string | null = null,
): void {
  context.callId = callId;
  context.roomName = roomName;
}

export function logUiTelemetry(
  event: string,
  detail?: Record<string, unknown>,
): void {
  const payload = {
    event,
    ts: Date.now(),
    call_id: context.callId,
    room_name: context.roomName,
    ...detail,
  };
  console.log(PREFIX, event, payload);
  void postUiTelemetry(payload).catch(() => {
    /* non-blocking; console remains the fallback */
  });
}
