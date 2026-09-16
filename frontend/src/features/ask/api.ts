/**
 * Ask-screen API — the same gateway paths the Playground uses. Identity is the
 * bearer token only; Kong injects the trusted X-* headers.
 */
import { API_URL } from '../../lib/config';
import { apiGet } from '../../lib/apiClient';
import keycloak from '../../lib/keycloak';
import type { AgentOption, ClarificationAnswer } from './types';

const authHeaders = (): Record<string, string> =>
  keycloak?.token ? { Authorization: `Bearer ${keycloak.token}` } : {};

interface OrchestratePayload {
  query: string;
  agent_id?: string;
  user_id: string;
  messages: Array<{ role: 'user' | 'assistant'; content: string }>;
  /** single-agent mode: the agent conversation this turn continues */
  conversation_id?: string;
  /** options picked on the previous turn's clarification card */
  clarification_answers?: ClarificationAnswer[];
}

async function openStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText || 'stream unavailable'}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = String(j.detail);
    } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return res;
}

/** POST /api/orchestrate/stream — returns the raw Response; caller reads the SSE body. */
export function openOrchestrateStream(payload: OrchestratePayload, signal?: AbortSignal): Promise<Response> {
  return openStream('/api/orchestrate/stream', payload, signal);
}

/**
 * POST /api/orchestrate/resume/stream — continue an interrupted orchestration
 * with the user's clarification. Streams the same frames as /stream, so the
 * resumed answer gets its charts and evidence, and a card if it asks again.
 */
export function openResumeStream(sessionId: string, clarification: string, signal?: AbortSignal): Promise<Response> {
  return openStream('/api/orchestrate/resume/stream', { session_id: sessionId, clarification }, signal);
}

/** GET /api/catalog/agents — the scopes a question can be asked in today. */
export async function fetchAgents(): Promise<AgentOption[]> {
  const data = await apiGet<Array<{ id: string; name: string; description: string; routing_keywords?: string[] }>>('/api/catalog/agents');
  return data.map(a => ({ id: a.id, label: a.name, description: a.description, routing_keywords: a.routing_keywords }));
}

interface ResultRowsResponse {
  result_id: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export async function fetchResultRows(
  resultId: string,
  params: { offset?: number; limit?: number; sort?: string; dir?: 'asc' | 'desc' } = {},
): Promise<ResultRowsResponse> {
  const qs = new URLSearchParams();
  if (params.offset !== undefined) qs.set('offset', String(params.offset));
  if (params.limit !== undefined) qs.set('limit', String(params.limit));
  if (params.sort) qs.set('sort', params.sort);
  if (params.dir) qs.set('dir', params.dir);
  const suffix = qs.toString() ? `?${qs}` : '';
  return apiGet<ResultRowsResponse>(`/api/ask/results/${resultId}/rows${suffix}`);
}

export async function downloadResultXlsx(resultId: string, fileName?: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/ask/results/${resultId}/export.xlsx`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText || 'export unavailable'}`);
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function' || typeof document === 'undefined') return false;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName || `ask-result-${resultId.slice(0, 8)}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return true;
}
