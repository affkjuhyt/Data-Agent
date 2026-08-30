import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const API = '/api/auth';

interface IPRule {
  id: string;
  cidr: string;
  label: string;
  is_active: boolean;
  created_by: string;
  created_at: string;
}

export type IPMode = 'allowlist' | 'allow_all';

interface Result {
  rules: IPRule[];
  mode: IPMode;
  loading: boolean;
  error: string | null;
  addRule: (cidr: string, label: string) => Promise<void>;
  deleteRule: (id: string) => Promise<void>;
  toggleRule: (id: string) => Promise<void>;
  setMode: (mode: IPMode) => Promise<void>;
  refetch: () => void;
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

export const useIPAllowlist = (): Result => {
  const [rules, setRules]     = useState<IPRule[]>([]);
  const [mode, setModeState]  = useState<IPMode>('allowlist');
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    if (!keycloak.token) return;
    setLoading(true);
    setError(null);
    try {
      const [rulesRes, configRes] = await Promise.all([
        apiFetch(`${API}/ip-allowlists`),
        apiFetch(`${API}/ip-allowlist/config`),
      ]);
      setRules(await rulesRes.json());
      const cfg = await configRes.json();
      setModeState(cfg.mode ?? 'allowlist');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load IP allowlist');
    } finally {
      setLoading(false);
    }
  }, [keycloak.token]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const addRule = useCallback(async (cidr: string, label: string) => {
    const res = await apiFetch(`${API}/ip-allowlists`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cidr, label }),
    });
    const created: IPRule = await res.json();
    setRules(prev => [created, ...prev]);
  }, []);

  const deleteRule = useCallback(async (id: string) => {
    await apiFetch(`${API}/ip-allowlists/${id}`, { method: 'DELETE' });
    setRules(prev => prev.filter(r => r.id !== id));
  }, []);

  const toggleRule = useCallback(async (id: string) => {
    const res = await apiFetch(`${API}/ip-allowlists/${id}/toggle`, { method: 'PATCH' });
    const updated: IPRule = await res.json();
    setRules(prev => prev.map(r => r.id === id ? updated : r));
  }, []);

  const setMode = useCallback(async (newMode: IPMode) => {
    const res = await apiFetch(`${API}/ip-allowlist/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: newMode }),
    });
    const cfg = await res.json();
    setModeState(cfg.mode);
  }, []);

  return { rules, mode, loading, error, addRule, deleteRule, toggleRule, setMode, refetch: fetch_ };
};
