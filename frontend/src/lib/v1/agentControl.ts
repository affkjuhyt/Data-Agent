import { pathId, v1Send } from './client';
import type { AgentDef } from './types';

/*
 * Locking an agent stops every run and trigger of the agent and hands its open HITL requests to
 * the owner; the agent is then `locked`, with the reason in paused_reason, and unlocking restores
 * the status it had. The API requires a reason; the confirmation dialog currently supplies the standard
 * UI reason because it does not collect a separate free-text explanation.
 */
export const lockAgent = async (id: string) =>
  v1Send<AgentDef>('POST', `/agents/${pathId(id)}:lock`, {
    reason: 'Khoá theo yêu cầu của người dùng.',
  });

export const unlockAgent = async (id: string) =>
  v1Send<AgentDef>('POST', `/agents/${pathId(id)}:unlock`);

export const isLocked = (agent: AgentDef) => agent.status === 'locked';
