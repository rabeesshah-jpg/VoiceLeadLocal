import { log, logError, logWarn } from '../lib/logger';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const DEV_KEY = import.meta.env.VITE_VOICE_AGENT_DEV_API_KEY || 'dev-local-key';

export type VoiceProfileStatus = 'pending' | 'processing' | 'ready' | 'failed';

export type VoiceProfileSourceType =
  | 'reference_audio'
  | 'voice_builder_json'
  | 'preset'
  | 'unknown';

export interface VoiceProfile {
  id: string;
  name: string;
  status: VoiceProfileStatus;
  source_type: VoiceProfileSourceType;
  provider: string;
  provider_voice_id: string;
  error_message: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface VoiceProfileListResponse {
  voice_cloning_enabled: boolean;
  supports_reference_audio_cloning?: boolean;
  supports_voice_builder_json?: boolean;
  capabilities_message?: string;
  profiles: VoiceProfile[];
}

function authHeaders(): HeadersInit {
  return {
    'X-Voice-Agent-Dev-Key': DEV_KEY,
  };
}

async function parseError(res: Response, step: string): Promise<string> {
  const err = await res.json().catch(() => ({}));
  const message =
    (err as { error?: string }).error ||
    (err as { detail?: string }).detail ||
    `Request failed (HTTP ${res.status})`;
  logWarn(`${step}_api_error`, { status: res.status, body: err });
  return message;
}

export async function listVoiceProfiles(): Promise<VoiceProfileListResponse> {
  const url = `${API_BASE}/api/voice-profiles/`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(await parseError(res, 'list_voice_profiles'));
  }
  const body = (await res.json()) as VoiceProfileListResponse;
  log('voice_profiles_loaded', {
    count: body.profiles.length,
    supportsAudioClone: body.supports_reference_audio_cloning,
    supportsJsonUpload: body.supports_voice_builder_json,
  });
  return body;
}

export async function cloneVoiceProfile(
  name: string,
  audioBlob: Blob,
  filename: string,
  consentConfirmed: boolean,
): Promise<VoiceProfile> {
  const url = `${API_BASE}/api/voice-profiles/clone/`;
  const form = new FormData();
  form.append('name', name);
  form.append('audio', audioBlob, filename);
  form.append('consent_confirmed', consentConfirmed ? 'true' : 'false');

  log('voice_profile_clone_request', { name, filename, size: audioBlob.size });
  const res = await fetch(url, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });

  const body = (await res.json().catch(() => ({}))) as VoiceProfile & { error?: string };

  if (!res.ok) {
    const message = body.error || (await parseError(res, 'clone_voice_profile'));
    logError('voice_profile_clone_failed', { message, status: res.status, body });
    throw new Error(message);
  }

  if (!isVoiceProfileUsable(body)) {
    logWarn('voice_profile_clone_not_ready', {
      id: body.id,
      status: body.status,
      providerVoiceId: body.provider_voice_id || null,
      error: body.error_message,
    });
    throw new Error(
      body.error_message ||
        `Voice clone not ready (status=${body.status}). provider_voice_id missing.`,
    );
  }

  log('voice_profile_clone_success', {
    id: body.id,
    status: body.status,
    providerVoiceId: body.provider_voice_id,
    name: body.name,
  });
  return body;
}

export async function uploadJsonVoiceProfile(
  file: File,
  displayName: string,
  consentConfirmed: boolean,
): Promise<VoiceProfile> {
  const url = `${API_BASE}/api/voice-profiles/upload-json/`;
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('display_name', displayName);
  form.append('consent_confirmed', consentConfirmed ? 'true' : 'false');

  log('voice_profile_json_upload_request', {
    displayName,
    filename: file.name,
    size: file.size,
  });
  const res = await fetch(url, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  });

  const body = (await res.json().catch(() => ({}))) as VoiceProfile & { error?: string };

  if (!res.ok) {
    const message = body.error || (await parseError(res, 'upload_json_voice_profile'));
    logError('voice_profile_json_upload_failed', { message, status: res.status, body });
    throw new Error(message);
  }

  if (!isVoiceProfileUsable(body)) {
    logWarn('voice_profile_json_upload_not_ready', {
      id: body.id,
      status: body.status,
      providerVoiceId: body.provider_voice_id || null,
      error: body.error_message,
    });
    throw new Error(
      body.error_message ||
        `Voice JSON upload not ready (status=${body.status}). provider_voice_id missing.`,
    );
  }

  log('voice_profile_json_upload_success', {
    id: body.id,
    status: body.status,
    providerVoiceId: body.provider_voice_id,
    name: body.name,
  });
  return body;
}

export async function testVoiceProfile(profileId: string): Promise<Blob> {
  const url = `${API_BASE}/api/voice-profiles/${profileId}/test/`;
  log('voice_profile_test_request', { profileId });
  const res = await fetch(url, { method: 'POST', headers: authHeaders() });
  if (!res.ok) {
    const message = await parseError(res, 'test_voice_profile');
    logError('voice_profile_test_failed', { profileId, message });
    throw new Error(message);
  }
  const blob = await res.blob();
  log('voice_profile_test_success', { profileId, bytes: blob.size });
  return blob;
}

export async function refreshVoiceProfile(
  profileId: string,
  options: { wait?: boolean } = {},
): Promise<VoiceProfile> {
  const wait = options.wait ? '?wait=true' : '';
  const url = `${API_BASE}/api/voice-profiles/${profileId}/refresh/${wait}`;
  log('voice_profile_refresh_request', { profileId, wait: options.wait ?? false });
  const res = await fetch(url, { method: 'POST', headers: authHeaders() });
  if (!res.ok) {
    const message = await parseError(res, 'refresh_voice_profile');
    logError('voice_profile_refresh_failed', { profileId, message });
    throw new Error(message);
  }
  const profile = (await res.json()) as VoiceProfile;
  if (isVoiceProfileUsable(profile)) {
    log('voice_profile_refresh_success', {
      id: profile.id,
      status: profile.status,
      providerVoiceId: profile.provider_voice_id,
    });
  } else {
    logWarn('voice_profile_refresh_not_ready', {
      id: profile.id,
      status: profile.status,
      providerVoiceId: profile.provider_voice_id || null,
      error: profile.error_message,
    });
  }
  return profile;
}

export function isVoiceProfileUsable(profile: VoiceProfile): boolean {
  return profile.status === 'ready' && Boolean(profile.provider_voice_id?.trim());
}

export async function waitForVoiceProfileReady(
  profileId: string,
  maxAttempts = 30,
  intervalMs = 2000,
): Promise<VoiceProfile> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const profile = await refreshVoiceProfile(profileId);
    if (isVoiceProfileUsable(profile)) {
      return profile;
    }
    if (profile.status === 'failed') {
      throw new Error(profile.error_message || 'Voice cloning failed on RunPod');
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(
    'RunPod voice cloning timed out after 60 seconds. The server may need SUPERTONE_API_KEY for recorded audio cloning.',
  );
}

export async function deleteVoiceProfile(profileId: string): Promise<void> {
  const url = `${API_BASE}/api/voice-profiles/${profileId}/`;
  const res = await fetch(url, { method: 'DELETE', headers: authHeaders() });
  if (!res.ok) {
    throw new Error(await parseError(res, 'delete_voice_profile'));
  }
  log('voice_profile_deleted', { profileId });
}

export async function setDefaultVoiceProfile(profileId: string): Promise<VoiceProfile> {
  const url = `${API_BASE}/api/voice-profiles/${profileId}/set-default/`;
  const res = await fetch(url, { method: 'POST', headers: authHeaders() });
  if (!res.ok) {
    throw new Error(await parseError(res, 'set_default_voice_profile'));
  }
  return (await res.json()) as VoiceProfile;
}
