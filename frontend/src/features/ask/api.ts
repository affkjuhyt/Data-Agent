/**
 * Ask-screen API — the same gateway paths the Playground uses. Identity is the
 * bearer token only; Kong injects the trusted X-* headers.
 */
import { API_URL } from '../../lib/config';
import { apiGet } from '../../lib/apiClient';
import keycloak from '../../lib/keycloak';
import type { AgentOption, OrchestrateResponse } from './types';

const authHeaders = (): Record<string, string> =>
  keycloak?.token ? { Authorization: `Bearer ${keycloak.token}` } : {};

interface OrchestratePayload {
  query: string;
  agent_id?: string;
  user_id: string;
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
}

/** POST /api/orchestrate/stream — returns the raw Response; caller reads the SSE body. */
export async function openOrchestrateStream(payload: OrchestratePayload, signal?: AbortSignal): Promise<Response> {
  const res = await fetch(`${API_URL}/api/orchestrate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`${res.status} ${res.statusText || 'stream unavailable'}`);
  }
  return res;
}

/**
 * POST /api/orchestrate/resume — continue an interrupted orchestration with
 * the user's clarification. Not through apiMutate: that client has a 3 s
 * timeout, and resuming runs the rest of the graph (tens of seconds on a
 * shared GPU). The whole answer comes back in one JSON body, not a stream.
 */
export async function resumeClarification(sessionId: string, clarification: string): Promise<OrchestrateResponse> {
  const res = await fetch(`${API_URL}/api/orchestrate/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ session_id: sessionId, clarification }),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = String(j.detail);
    } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return (await res.json()) as OrchestrateResponse;
}

/** GET /api/catalog/agents — the scopes a question can be asked in today. */
export async function fetchAgents(): Promise<AgentOption[]> {
  const data = await apiGet<Array<{ id: string; name: string; description: string; routing_keywords?: string[] }>>('/api/catalog/agents');
  return data.map(a => ({ id: a.id, label: a.name, description: a.description, routing_keywords: a.routing_keywords }));
}
