import { log, logError, logWarn } from '../lib/logger';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const DEV_KEY = import.meta.env.VITE_VOICE_AGENT_DEV_API_KEY || 'dev-local-key';

function headers(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Voice-Agent-Dev-Key': DEV_KEY,
  };
}

export interface StartCallResponse {
  call_id: string;
  room_name: string;
  livekit_url: string;
  participant_token: string;
  participant_identity: string;
  expires_at: string;
}

function describeFetchError(err: unknown, url: string): string {
  if (err instanceof TypeError && /fetch|network|failed/i.test(err.message)) {
    return (
      `Network error calling ${url}. ` +
      'If the console shows a CORS error, restart the API after updating CORS headers. ' +
      `Otherwise ensure the API is running (${API_BASE || 'set VITE_API_BASE_URL'}).`
    );
  }
  return err instanceof Error ? err.message : 'Unknown error';
}

async function apiFetch(
  path: string,
  init: RequestInit,
  step: string,
): Promise<Response> {
  const url = `${API_BASE}${path}`;
  log(`${step}_request`, {
    url,
    method: init.method || 'GET',
    hasDevKey: Boolean(DEV_KEY),
    apiBaseConfigured: Boolean(API_BASE),
  });

  const t0 = performance.now();
  try {
    const res = await fetch(url, init);
    log(`${step}_response`, {
      url,
      status: res.status,
      ok: res.ok,
      elapsedMs: Math.round(performance.now() - t0),
      requestId: res.headers.get('X-Request-Id'),
    });
    return res;
  } catch (err) {
    logError(`${step}_network_error`, {
      url,
      message: err instanceof Error ? err.message : String(err),
      elapsedMs: Math.round(performance.now() - t0),
    });
    throw new Error(describeFetchError(err, url));
  }
}

export interface StartCallOptions {
  system_prompt?: string;
  /** male → Michael.wav, female → Olivia.wav (+ TTS expressiveness params) */
  persona_id?: 'male' | 'female' | '';
}

export async function startCall(options: StartCallOptions = {}): Promise<StartCallResponse> {
  const res = await apiFetch(
    '/api/calls/start/',
    {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({
        system_prompt: options.system_prompt || '',
        persona_id: options.persona_id || '',
      }),
    },
    'start_call',
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const message =
      (err as { error?: string }).error ||
      (err as { detail?: string }).detail ||
      `Failed to start call (HTTP ${res.status})`;
    logWarn('start_call_api_error', { status: res.status, body: err });
    throw new Error(message);
  }

  const body = (await res.json()) as StartCallResponse;
  log('start_call_success', {
    callId: body.call_id,
    room: body.room_name,
    livekitUrl: body.livekit_url,
    identity: body.participant_identity,
    tokenLength: body.participant_token?.length ?? 0,
  });
  return body;
}

export async function endCall(callId: string, reason = 'user_ended'): Promise<void> {
  const res = await apiFetch(
    `/api/calls/${callId}/end/`,
    {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ reason }),
    },
    'end_call',
  );
  if (!res.ok) {
    logWarn('end_call_failed', { callId, status: res.status });
    throw new Error('Failed to end call');
  }
  log('end_call_success', { callId, reason });
}

export async function getHealth(): Promise<{
  status: string;
  livekit_configured?: boolean;
  providers_configured?: boolean;
  missing?: string[];
}> {
  const res = await apiFetch('/api/health/', { method: 'GET' }, 'health');
  const body = await res.json();
  log('health_check', body);
  return body;
}
