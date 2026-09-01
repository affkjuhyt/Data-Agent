/**
 * Read-only platform data for the Settings module. Every hook here wraps a
 * single real endpoint behind Kong (apiGet) — no fixtures, no fallbacks other
 * than "empty". Failures surface as `error` so screens render ErrorState.
 */
import { useCallback, useEffect, useState } from 'react';
import { apiGet } from './apiClient';
import { WORKFLOW_RUNTIME_URL } from './config';

interface ApiResource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

/** Generic GET-and-hold. `enabled=false` skips the request (renders empty). */
function useApiResource<T>(
  path: string,
  params?: Record<string, string | number | boolean>,
  enabled = true,
): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const paramsKey = params ? JSON.stringify(params) : '';

  const reload = useCallback(async () => {
    if (!enabled) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<T>(path, params));
    } catch (e) {
      setError(e instanceof Error ? e.message : `GET ${path} failed`);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, paramsKey, enabled]);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, error, reload };
}

/* ─── /api/platform/metrics ───────────────────────────────── */
interface PlatformMetrics {
  agents: { total: number; published: number };
  conversations: { total: number; last_24h: number; active: number };
  messages: { total: number; last_24h: number };
  tokens: { total: number; last_24h: number };
  latency_ms: { avg: number; p95: number };
  error_rate: number;
  series: Array<Record<string, number | string>>;
}
export const usePlatformMetrics = (days = 7) =>
  useApiResource<PlatformMetrics>('/api/platform/metrics', { days });

/* ─── /api/platform/services ──────────────────────────────── */
export type ServiceKind = 'database' | 'cache' | 'queue' | 'llm' | 'service';
export type ServiceStatus = 'ok' | 'degraded' | 'down' | 'unknown';
export interface PlatformService {
  name: string;
  kind: ServiceKind;
  status: ServiceStatus;
  latency_ms: number | null;
  detail: string | null;
}
export const usePlatformServices = () =>
  useApiResource<{ items: PlatformService[] }>('/api/platform/services');

/* ─── /api/auth/me/tenants ────────────────────────────────── */
interface Tenant { id: string; name: string; slug: string }
export const usePlatformTenants = () => useApiResource<Tenant[]>('/api/auth/me/tenants');

/* ─── /api/auth/me/roles ──────────────────────────────────── */
export const usePlatformRoles = () => useApiResource<{ roles: string[] }>('/api/auth/me/roles');

/* ─── /api/runtime-config ─────────────────────────────────── */
export const usePlatformRuntimeConfig = () =>
  useApiResource<Record<string, unknown>>('/api/runtime-config');

/* ─── /api/storage/buckets (workflow_runtime) ─────────────── */
export const usePlatformBuckets = () =>
  useApiResource<{ buckets: string[] }>('/api/storage/buckets');

interface BucketObject { key: string; size: number; last_modified: string }
export const usePlatformBucketObjects = (bucket: string | null) =>
  useApiResource<{ bucket: string; objects: BucketObject[] }>(
    `/api/storage/buckets/${encodeURIComponent(bucket ?? '')}/objects`,
    undefined,
    !!bucket,
  );

/* ─── /api/knowledge/qdrant/collections (kb_backend) ──────── */
/** Shape from kb_backend operations/qdrant_ops.list_collections. */
interface VectorCollection {
  id: string;
  name: string;
  /** Points belonging to the current tenant. */
  points: number;
  total_points: number;
  active: boolean;
  dimensions: number;
  distance: string;
  /** Indexed percentage 0–100. */
  indexed: number;
  embedding_model: string;
}
export const usePlatformVectorCollections = () =>
  useApiResource<VectorCollection[]>('/api/knowledge/qdrant/collections');

/* ─── /api/sandbox/status (workflow_runtime relay) ────────── */
export interface SandboxJobSummary {
  job_id: string;
  kind: 'python' | 'sql';
  status: 'queued' | 'running' | 'done' | 'rejected' | 'error';
  age_s: number;
  duration_s: number | null;
  error: string | null;
}
interface SandboxStatus {
  pool: {
    size: number;
    idle: number;
    busy: number;
    egress_enabled: boolean;
    runtime: string;
    circuit_breaker: { state: 'closed' | 'open' | 'half_open'; enforce: boolean };
  };
  jobs: { python: SandboxJobSummary[]; sql: SandboxJobSummary[] };
}

/** Mirrors SandboxQueuePanel: workflow_runtime relay, same base URL. */
export const usePlatformSandboxStatus = (): ApiResource<SandboxStatus> => {
  const [data, setData] = useState<SandboxStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${WORKFLOW_RUNTIME_URL}/api/sandbox/status`, { signal: AbortSignal.timeout(5000) });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'sandbox_executor unreachable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, error, reload };
};
