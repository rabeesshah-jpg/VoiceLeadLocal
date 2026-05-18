import { describe, expect, it } from 'vitest';

describe('agent data messages', () => {
  it('parses agent_state payload', () => {
    const raw = JSON.stringify({ type: 'agent_state', state: 'listening' });
    const msg = JSON.parse(raw);
    expect(msg.type).toBe('agent_state');
    expect(msg.state).toBe('listening');
  });
});
