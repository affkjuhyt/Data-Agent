/**
 * Runtime config the flow builder reads from workflow-runtime instead of
 * hardcoding model names and pipeline constants in the component.
 *
 *   GET /api/llm-providers   → available chat models (with is_default)
 *   GET /api/runtime-config  → { rrf_k }
 *
 * These catalog endpoints are served directly by workflow-runtime (no auth),
 * matching how Builder already fetches /api/nodes/registry.
 */
import { useEffect, useState } from 'react';

import { apiGet } from './apiClient';

interface LlmProvider {
  id: string;
  name: string;
  model_id: string;
  type: string;
  is_default: boolean;
}

interface RuntimeConfig {
  rrf_k: number;
}

// Memoize so multiple components (each ConfigPanel mount) reuse one fetch per
// page load instead of hitting workflow-runtime every time a node is opened.
// A failed fetch clears the cache so the next caller retries.
let _providersPromise: Promise<LlmProvider[]> | null = null;
let _configPromise: Promise<RuntimeConfig> | null = null;

function fetchLlmProviders(): Promise<LlmProvider[]> {
  if (!_providersPromise) {
    _providersPromise = apiGet<LlmProvider[]>('/api/llm-providers')
      .catch(err => {
        _providersPromise = null;
        throw err;
      });
  }
  return _providersPromise;
}

function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  if (!_configPromise) {
    _configPromise = apiGet<RuntimeConfig>('/api/runtime-config')
      .catch(err => {
        _configPromise = null;
        throw err;
      });
  }
  return _configPromise;
}

interface RuntimeMeta {
  /** model_id → display name, e.g. { planner: 'Claude Haiku', responder: 'Claude Sonnet' }. */
  models: Record<string, string>;
  rrfK: number | null;
  loading: boolean;
}

/** Loads model catalog + runtime config once and exposes them to the UI. */
export function useRuntimeConfig(): RuntimeMeta {
  const [models, setModels] = useState<Record<string, string>>({});
  const [rrfK, setRrfK] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([fetchLlmProviders(), fetchRuntimeConfig()]).then(([p, c]) => {
      if (cancelled) return;
      if (p.status === 'fulfilled') {
        const map: Record<string, string> = {};
        for (const provider of p.value) map[provider.model_id] = provider.name;
        setModels(map);
      }
      if (c.status === 'fulfilled') setRrfK(c.value.rrf_k);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return { models, rrfK, loading };
}
