/**
 * LLM provider catalogue, model registry and live health — all from
 * workflow_runtime behind Kong:
 *
 *   GET /api/llm-providers         → [{ id, name, model_id, type, is_default }]
 *   GET /api/llm-providers/models  → { items: [...], available }
 *   GET /api/llm-providers/health  → { items: [{ model_name, status, latency_ms, error }], available }
 */
import { useCallback, useEffect, useState } from 'react';
import { apiGet } from './apiClient';

export interface LlmProvider {
  id: string;
  name: string;
  model_id: string;
  type: string;
  is_default: boolean;
}

export interface LlmModel {
  model_name: string;
  litellm_provider: string;
  base_model: string | null;
  max_tokens: number | null;
  input_cost_per_token: number | null;
  output_cost_per_token: number | null;
  mode: string;
}

export interface LlmHealth {
  model_name: string;
  status: 'ok' | 'down';
  latency_ms: number | null;
  error: string | null;
}

interface Result {
  providers: LlmProvider[];
  models: LlmModel[];
  modelsAvailable: boolean;
  health: LlmHealth[];
  healthAvailable: boolean;
  loading: boolean;
  /** Per-endpoint errors; a failing sub-call must not hide the others. */
  errors: { providers: string | null; models: string | null; health: string | null };
  reload: () => Promise<void>;
  healthFor: (name: string) => LlmHealth | undefined;
  modelFor: (name: string) => LlmModel | undefined;
}

const msg = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

export const useLlmProviders = (): Result => {
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [models, setModels] = useState<LlmModel[]>([]);
  const [modelsAvailable, setModelsAvailable] = useState(false);
  const [health, setHealth] = useState<LlmHealth[]>([]);
  const [healthAvailable, setHealthAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState<Result['errors']>({ providers: null, models: null, health: null });

  const reload = useCallback(async () => {
    setLoading(true);
    const [p, m, h] = await Promise.allSettled([
      apiGet<LlmProvider[]>('/api/llm-providers'),
      apiGet<{ items: LlmModel[]; available: boolean }>('/api/llm-providers/models'),
      apiGet<{ items: LlmHealth[]; available: boolean }>('/api/llm-providers/health'),
    ]);
    const next: Result['errors'] = { providers: null, models: null, health: null };

    if (p.status === 'fulfilled') setProviders(Array.isArray(p.value) ? p.value : []);
    else { setProviders([]); next.providers = msg(p.reason, 'GET /api/llm-providers failed'); }

    if (m.status === 'fulfilled') { setModels(m.value?.items ?? []); setModelsAvailable(!!m.value?.available); }
    else { setModels([]); setModelsAvailable(false); next.models = msg(m.reason, 'GET /api/llm-providers/models failed'); }

    if (h.status === 'fulfilled') { setHealth(h.value?.items ?? []); setHealthAvailable(!!h.value?.available); }
    else { setHealth([]); setHealthAvailable(false); next.health = msg(h.reason, 'GET /api/llm-providers/health failed'); }

    setErrors(next);
    setLoading(false);
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const healthFor = useCallback(
    (name: string) => health.find(x => x.model_name === name),
    [health],
  );
  const modelFor = useCallback(
    (name: string) => models.find(x => x.model_name === name),
    [models],
  );

  return { providers, models, modelsAvailable, health, healthAvailable, loading, errors, reload, healthFor, modelFor };
};
