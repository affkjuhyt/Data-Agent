import { pathId, v1Send } from './client';
import type { AgentDef } from './types';

/*
 * PROPOSAL: locking an agent ("Xác nhận · lockAgent", 2094:8344). A2 has no endpoint for it; the
 * screen needs one that stops every run and trigger of the agent and hands its open HITL requests
 * to the owner. The agent comes back paused with paused_reason "locked"; :unlock reverses it.
 */
export const lockAgent = async (id: string) =>
  v1Send<AgentDef>('POST', `/agents/${pathId(id)}:lock`);

export const unlockAgent = async (id: string) =>
  v1Send<AgentDef>('POST', `/agents/${pathId(id)}:unlock`);

export const isLocked = (agent: AgentDef) =>
  agent.status === 'paused' && agent.paused_reason === 'locked';
