import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const API = '/api/auth';

interface APIKey {
  id: string;
  name: string;
  key_prefix: string;
  scope: string;
  created_by: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  rotated_from: string | null;
  created_at: string;
}

export interface NewKeyResult extends APIKey {
  raw_key: string;
}

interface Result {
  keys: APIKey[];
  loading: boolean;
  error: string | null;
  createKey: (name: string, scope: string, expires_at?: string) => Promise<NewKeyResult>;
  revokeKey: (id: string) => Promise<void>;
  rotateKey: (id: string) => Promise<NewKeyResult>;
  refetch: () => void;
}

function authHeaders() {
  return { Authorization: `Bearer ${keycloak.token}` };
}

export const useAPIKeys = (): Result => {
  const [keys, setKeys]       = useState<APIKey[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    if (!keycloak.token) return;
    setLoading(true);
    setError(null);
    try {
      await keycloak.updateToken(30).catch(() => {});
      const res = await fetch(`${API}/api-keys`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`Failed to load API keys (${res.status})`);
      setKeys(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load API keys');
    } finally {
      setLoading(false);
    }
  }, [keycloak.token]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const createKey = useCallback(async (name: string, scope: string, expires_at?: string): Promise<NewKeyResult> => {
    await keycloak.updateToken(30).catch(() => {});
    const res = await fetch(`${API}/api-keys`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, scope, expires_at: expires_at ?? null }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Error ${res.status}`);
    }
    const created: NewKeyResult = await res.json();
    setKeys(prev => [created, ...prev]);
    return created;
  }, []);

  const revokeKey = useCallback(async (id: string) => {
    await keycloak.updateToken(30).catch(() => {});
    const res = await fetch(`${API}/api-keys/${id}/revoke`, {
      method: 'POST',
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Error ${res.status}`);
    const updated: APIKey = await res.json();
    setKeys(prev => prev.map(k => k.id === id ? updated : k));
  }, []);

  const rotateKey = useCallback(async (id: string): Promise<NewKeyResult> => {
    await keycloak.updateToken(30).catch(() => {});
    const res = await fetch(`${API}/api-keys/${id}/rotate`, {
      method: 'POST',
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`Error ${res.status}`);
    const result: NewKeyResult = await res.json();
    // Replace old (now revoked) + add new
    setKeys(prev => {
      const revoked = prev.find(k => k.id === id);
      if (!revoked) return [result, ...prev];
      const updated = prev.map(k => k.id === id ? { ...k, revoked_at: new Date().toISOString() } : k);
      return [result, ...updated];
    });
    return result;
  }, []);

  return { keys, loading, error, createKey, revokeKey, rotateKey, refetch: fetch_ };
};
