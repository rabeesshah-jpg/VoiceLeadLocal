const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const DEV_KEY = import.meta.env.VITE_VOICE_AGENT_DEV_API_KEY || 'dev-local-key';

function headers(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Voice-Agent-Dev-Key': DEV_KEY,
  };
}

export async function postUiTelemetry(
  body: Record<string, unknown>,
): Promise<void> {
  if (!API_BASE) return;
  const url = `${API_BASE.replace(/\/$/, '')}/api/ui-telemetry/`;
  await fetch(url, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(body),
    keepalive: true,
  });
}
