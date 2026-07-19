import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const API = '/api/auth';

export const PII_TYPES = ['EMAIL', 'PHONE', 'CCCD', 'CMND', 'CREDIT_CARD', 'IP'] as const;
export type PiiType = (typeof PII_TYPES)[number];

export interface ComplianceSettings {
  pii_masking: boolean;
  pii_types: string[];
  residency_region: string;
  residency_standards: string;
  retention_days: number;
  retention_lock: boolean;
  zero_trust: boolean;
  audit_residency: boolean;
  updated_at: string | null;
  updated_by: string;
}

export interface CompliancePolicy {
  id: string;
  name: string;
  module: string;
  status: string;
  severity: string;
}

export interface ComplianceScore {
  score: number;
  breakdown: Array<{ label: string; val: number }>;
  policies: CompliancePolicy[];
}

export interface PiiScanResult {
  counts: Record<string, number>;
  total: number;
  preview: string;
}

export interface ReprocessResult {
  total: number;
  enqueued: number;
  failed: number;
  data_ids: string[];
  failed_ids: string[];
}

interface Result {
  settings: ComplianceSettings | null;
  score: ComplianceScore | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  updateSettings: (patch: Partial<ComplianceSettings>) => Promise<void>;
  scanPii: (text: string, types?: string[]) => Promise<PiiScanResult>;
  reprocessPii: () => Promise<ReprocessResult>;
  reload: () => Promise<void>;
}

function authHeaders() {
  return { Authorization: `Bearer ${keycloak.token}` };
}

async function apiFetch(url: string, init?: RequestInit) {
  await keycloak.updateToken(30).catch(() => {});
  const res = await fetch(url, { ...init, headers: { ...authHeaders(), ...(init?.headers ?? {}) } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Error ${res.status}`);
  }
  return res;
}

export const useCompliance = (): Result => {
  const [settings, setSettings] = useState<ComplianceSettings | null>(null);
  const [score, setScore]       = useState<ComplianceScore | null>(null);
  const [loading, setLoading]   = useState(false);
  const [saving, setSaving]     = useState(false);
  const [error, setError]       = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!keycloak.token) return;
    setLoading(true);
    setError(null);
    try {
      const [sRes, scRes] = await Promise.all([
        apiFetch(`${API}/compliance/settings`),
        apiFetch(`${API}/compliance/score`),
      ]);
      setSettings(await sRes.json());
      setScore(await scRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load compliance settings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const updateSettings = useCallback(async (patch: Partial<ComplianceSettings>) => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`${API}/compliance/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      setSettings(await res.json());
      // Score is derived from settings — refresh it after any change.
      const scRes = await apiFetch(`${API}/compliance/score`);
      setScore(await scRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
      throw e;
    } finally {
      setSaving(false);
    }
  }, []);

  const scanPii = useCallback(async (text: string, types?: string[]): Promise<PiiScanResult> => {
    const res = await apiFetch(`${API}/compliance/pii-scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, types }),
    });
    return res.json();
  }, []);

  const reprocessPii = useCallback(async (): Promise<ReprocessResult> => {
    // kb-backend (via Kong proxy) — re-runs Gold masking on existing documents.
    const res = await apiFetch(`/api/pipeline/ingestion/reprocess-pii`, { method: 'POST' });
    return res.json();
  }, []);

  return { settings, score, loading, saving, error, updateSettings, scanPii, reprocessPii, reload };
};
