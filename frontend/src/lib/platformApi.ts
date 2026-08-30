/**
 * Platform / observability API — shared types and hooks for the ops screens
 * (conversations, traces, platform metrics, service health, key-value
 * settings, tenants). Everything goes through apiClient (Kong + Bearer).
 *
 * Contract (workflow_runtime, auth_api, flow_buider):
 *   GET /api/conversations?limit&offset&status&q&agent_id → { items, total }
 *   GET /api/conversations/{id}/messages                → { messages }
 *   GET /api/traces?limit&offset&status&q               → { items, total }
 *   GET /api/platform/metrics?days=7                    → PlatformMetrics
 *   GET /api/platform/services                          → { items }
 *   GET|PUT /api/platform/settings/{key}                → { key, value, updated_at }
 *   GET /api/catalog/agents                             → CatalogAgent[]
 *   GET /api/agents, /api/agents/{id}                   → AgentSummary[], AgentDetail
 *   GET /api/auth/me/tenants                            → Tenant[]
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet, apiMutate } from './apiClient';

/* ─── Types ──────────────────────────────────────────────── */
export interface CatalogAgent { id: string; name: string; description?: string; routing_keywords?: string[] }

export interface AgentSummary {
  id: string;
  name: string;
  description: string;
  published_version_id: string | null;
  draft_version_id: string | null;
  created_at: string;
}

export interface AgentWorkflow {
  id: string;
  name: string;
  draft_version_id: string | null;
  published_version_id: string | null;
  created_at?: string;
}

export interface AgentDetail extends AgentSummary {
  updated_at?: string;
  active_workflow_id: string | null;
  active_workflow_name: string | null;
  workflows?: AgentWorkflow[];
  responder_model_id: string | null;
  system_prompt_id: string | null;
  memory_enabled: boolean;
}

export interface LLMProvider { id: string; name: string; model_id: string; type: string }
export interface SystemPrompt { id: string; name: string }

export interface ConversationItem {
  id: string;
  user_ref: string | null;
  channel: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  message_count: number;
  last_message_at: string | null;
  agent_id: string | null;
  agent_name: string | null;
  tokens: number | null;
  avg_latency_ms: number | null;
}
export interface ConversationsResponse { items: ConversationItem[]; total: number }

export interface MessageTrace {
  trace_index: number;
  tool_name: string;
  input: unknown;
  output: unknown;
  status: string;
  latency_ms: number | null;
}
export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
  tokens_used: number | null;
  latency_ms: number | null;
  traces: MessageTrace[];
}
export interface MessagesResponse { messages: ConversationMessage[] }

export interface TraceItem {
  id: string;
  conversation_id: string;
  message_id: string;
  tool_name: string;
  status: string;
  latency_ms: number | null;
  created_at: string;
  input: unknown;
  output: unknown;
  user_ref: string | null;
}
export interface TracesResponse { items: TraceItem[]; total: number }

export interface MetricsPoint {
  date: string;
  conversations: number;
  messages: number;
  tokens: number;
  avg_latency_ms: number | null;
  errors: number;
}
export interface PlatformMetrics {
  agents: { total: number; published: number };
  conversations: { total: number; last_24h: number; active: number };
  messages: { total: number; last_24h: number };
  tokens: { total: number; last_24h: number };
  latency_ms: { avg: number | null; p95: number | null };
  error_rate: number | null;
  series: MetricsPoint[];
}

export type ServiceStatus = 'ok' | 'degraded' | 'down' | 'unknown';
export interface ServiceHealth {
  name: string;
  kind: string;
  status: ServiceStatus;
  latency_ms: number | null;
  detail: string | null;
}
export interface ServicesResponse { items: ServiceHealth[] }

export interface PlatformSetting<T> { key: string; value: T | null; updated_at: string | null }

export interface Schedule {
  id: string;
  name: string;
  workflow_id: string | null;
  cron: string;
  enabled: boolean;
  last_run: string | null;
  /* display-only extras, stored alongside the contract fields */
  agent_id?: string | null;
  workflow_name?: string | null;
}

export interface AgentTemplate { id: string; name: string; description: string; tags: string[] }

export interface Tenant { id: string; name: string; slug: string }

/* ─── Helpers ────────────────────────────────────────────── */
type Params = Record<string, string | number | boolean | null | undefined>;

/** Drop empty / null params so the query string only carries real filters. */
export const cleanParams = (params?: Params): Record<string, string | number | boolean> => {
  const out: Record<string, string | number | boolean> = {};
  if (!params) return out;
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    out[k] = v;
  }
  return out;
};

export const errorMessage = (e: unknown, fallback = 'Request failed'): string =>
  e instanceof Error ? e.message : fallback;

/** Milliseconds between two ISO timestamps; null when the end is unknown. */
export const durationMs = (start: string | null | undefined, end: string | null | undefined): number | null => {
  if (!start || !end) return null;
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  if (Number.isNaN(a) || Number.isNaN(b) || b < a) return null;
  return b - a;
};

/** Pick the tenant to activate: the persisted one when still available, else the first. */
export const pickTenant = (tenants: Tenant[], storedId: string | null | undefined): Tenant | null => {
  if (tenants.length === 0) return null;
  return tenants.find(t => t.id === storedId) ?? tenants[0];
};

export const shortId = (id: string | null | undefined, n = 8): string => (id ? id.slice(0, n) : '—');

/* ─── Generic GET hook ───────────────────────────────────── */
export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

/**
 * Fetch `path` with `params` and refetch whenever either changes.
 * Pass `null` as path to disable (e.g. a drawer that is closed).
 */
export function useApi<T>(path: string | null, params?: Params): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(path !== null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const paramKey = JSON.stringify(cleanParams(params));

  const refetch = useCallback(async () => {
    const my = ++seq.current;
    if (path === null) { setData(null); setLoading(false); setError(null); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<T>(path, JSON.parse(paramKey));
      if (my === seq.current) setData(res);
    } catch (e) {
      if (my === seq.current) { setError(errorMessage(e)); setData(null); }
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [path, paramKey]);

  useEffect(() => { void refetch(); }, [refetch]);

  return { data, loading, error, refetch };
}

/* ─── Typed hooks ────────────────────────────────────────── */
export type ConversationQuery = {
  limit?: number; offset?: number; status?: string; q?: string; agent_id?: string;
};
export const useConversations = (query: ConversationQuery) =>
  useApi<ConversationsResponse>('/api/conversations', query);

export const useConversationMessages = (conversationId: string | null) =>
  useApi<MessagesResponse>(conversationId ? `/api/conversations/${conversationId}/messages` : null);

export type TraceQuery = { limit?: number; offset?: number; status?: string; q?: string };
export const useTraces = (query: TraceQuery) => useApi<TracesResponse>('/api/traces', query);

export const usePlatformMetrics = (days: number) => useApi<PlatformMetrics>('/api/platform/metrics', { days });

export const usePlatformServices = () => useApi<ServicesResponse>('/api/platform/services');

export const useCatalogAgents = () => useApi<CatalogAgent[]>('/api/catalog/agents');

export const useAgents = () => useApi<AgentSummary[]>('/api/agents');

export const useAgentDetail = (agentId: string | null) =>
  useApi<AgentDetail>(agentId ? `/api/agents/${agentId}` : null);

/** Key-value platform setting with optimistic save (PUT whole value). */
export function usePlatformSetting<T>(key: string, fallback: T) {
  const state = useApi<PlatformSetting<T>>(`/api/platform/settings/${key}`);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [local, setLocal] = useState<T | null>(null);

  useEffect(() => { setLocal(null); }, [state.data]);

  const value: T = local ?? state.data?.value ?? fallback;

  const save = useCallback(async (next: T) => {
    setSaving(true);
    setSaveError(null);
    try {
      await apiMutate<PlatformSetting<T>>('PUT', `/api/platform/settings/${key}`, { value: next });
      setLocal(next);
      await state.refetch();
    } catch (e) {
      setSaveError(errorMessage(e));
      throw e;
    } finally {
      setSaving(false);
    }
  }, [key, state.refetch]);

  return { value, loading: state.loading, error: state.error, saving, saveError, save, refetch: state.refetch, updatedAt: state.data?.updated_at ?? null };
}
