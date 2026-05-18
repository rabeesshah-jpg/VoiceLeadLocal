const PREFIX = '[VoiceAgent]';

export function log(step: string, detail?: Record<string, unknown>) {
  if (detail) {
    console.log(PREFIX, step, detail);
  } else {
    console.log(PREFIX, step);
  }
}

export function logWarn(step: string, detail?: Record<string, unknown>) {
  if (detail) {
    console.warn(PREFIX, step, detail);
  } else {
    console.warn(PREFIX, step);
  }
}

export function logError(step: string, detail?: Record<string, unknown>) {
  if (detail) {
    console.error(PREFIX, step, detail);
  } else {
    console.error(PREFIX, step);
  }
}
