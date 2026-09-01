/**
 * Persisted platform settings — one JSON document per key, stored by
 * workflow_runtime behind Kong:
 *
 *   GET /api/platform/settings/{key} → { key, value | null, updated_at | null }
 *   PUT /api/platform/settings/{key} body { value } → same shape
 *
 * `value` is null until something has been saved; callers pass a `fallback`
 * that is used in that case (an empty list / empty form), never sample data.
 */
import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiMutate } from './apiClient';

interface PlatformSettingEnvelope<T> {
  key: string;
  value: T | null;
  updated_at: string | null;
}

interface PlatformSettingResult<T> {
  value: T;
  /** True once the server has a stored value (false = still on fallback). */
  persisted: boolean;
  updatedAt: string | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  save: (next: T) => Promise<void>;
  reload: () => Promise<void>;
}

export function usePlatformSetting<T>(key: string, fallback: T): PlatformSettingResult<T> {
  const [value, setValue] = useState<T>(fallback);
  const [persisted, setPersisted] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const env = await apiGet<PlatformSettingEnvelope<T>>(`/api/platform/settings/${encodeURIComponent(key)}`);
      if (env && env.value !== null && env.value !== undefined) {
        setValue(env.value);
        setPersisted(true);
      } else {
        setValue(fallback);
        setPersisted(false);
      }
      setUpdatedAt(env?.updated_at ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : `Không tải được cấu hình ${key}`);
    } finally {
      setLoading(false);
    }
    // `fallback` is intentionally not a dependency: callers pass literals.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => { reload(); }, [reload]);

  const save = useCallback(async (next: T) => {
    setSaving(true);
    setError(null);
    try {
      const env = await apiMutate<PlatformSettingEnvelope<T>>('PUT', `/api/platform/settings/${encodeURIComponent(key)}`, { value: next });
      setValue((env && env.value !== null && env.value !== undefined) ? env.value : next);
      setPersisted(true);
      setUpdatedAt(env?.updated_at ?? new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : `Không lưu được cấu hình ${key}`);
      throw e;
    } finally {
      setSaving(false);
    }
  }, [key]);

  return { value, persisted, updatedAt, loading, saving, error, save, reload };
}

/** Small id helper for list-type settings (no Math.random — time + counter). */
let _seq = 0;
export const newSettingId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(++_seq).toString(36)}`;
