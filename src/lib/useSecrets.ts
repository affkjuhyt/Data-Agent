import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const API = '/api/auth';

export interface Secret {
  id: string;
  key_name: string;
  key_type: string;
  algorithm: string;
  realm: string;
  version: number;
  is_active: boolean;
  rotation_due_at: string | null;
  last_rotated_at: string | null;
  created_by: string;
  created_at: string;
}

interface GovernanceConfig {
  vault_auto_rotation: boolean;
  vault_panic_mode: boolean;
  vault_pii_access_log: boolean;
}

interface AuditEntry {
  id: string;
  event: string;
  key_name: string;
  actor_id: string;
  old_version: number;
  new_version: number;
  status: string;
  time: string | null;
}

interface HsmStatus {
  transit_keys: Array<{ name: string; type: string; latest_version: number; min_version: number }>;
  key_count: number;
  openbao_sealed: boolean;
  mount: string;
}

interface PiiLogEntry {
  id: string;
  key_name: string;
  key_type: string;
  actor_id: string;
  version: number;
  access_reason: string | null;
  time: string | null;
}

export interface CreateSecretPayload {
  key_name: string;
  key_type: string;
  algorithm: string;
  realm: string;
  value: string;
  rotation_days?: number;
}

// Key types managed by Transit engine (key auto-generated inside vault)
export const TRANSIT_TYPES = new Set(['SIGNING_KEY', 'ENCRYPTION_KEY', 'HMAC_KEY']);

interface Result {
  secrets: Secret[];
  governance: GovernanceConfig;
  auditLog: AuditEntry[];
  piiLog: PiiLogEntry[];
  hsmStatus: HsmStatus | null;
  loading: boolean;
  error: string | null;
  createSecret: (payload: CreateSecretPayload) => Promise<Secret>;
  deleteSecret: (id: string) => Promise<void>;
  rotateSecret: (id: string, newValue: string) => Promise<Secret>;
  revealSecret: (id: string, accessReason?: string) => Promise<string>;
  signData: (id: string, data: string) => Promise<{ signature: string; key_version: number }>;
  verifyData: (id: string, data: string, signature: string) => Promise<{ valid: boolean }>;
  updateGovernance: (patch: Partial<GovernanceConfig>) => Promise<void>;
  triggerPanic: () => Promise<{ revoked: string[]; count: number }>;
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

const defaultGovernance: GovernanceConfig = {
  vault_auto_rotation: false,
  vault_panic_mode: false,
  vault_pii_access_log: false,
};

export const useSecrets = (): Result => {
  const [secrets, setSecrets]       = useState<Secret[]>([]);
  const [governance, setGovernance] = useState<GovernanceConfig>(defaultGovernance);
  const [auditLog, setAuditLog]     = useState<AuditEntry[]>([]);
  const [piiLog, setPiiLog]         = useState<PiiLogEntry[]>([]);
  const [hsmStatus, setHsmStatus]   = useState<HsmStatus | null>(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState<string | null>(null);

  const fetch_ = useCallback(async () => {
    if (!keycloak.token) return;
    setLoading(true);
    setError(null);
    try {
      const [secretsRes, govRes, auditRes, hsmRes, piiRes] = await Promise.all([
        apiFetch(`${API}/secrets`),
        apiFetch(`${API}/secrets/governance`),
        apiFetch(`${API}/secrets/audit-log`),
        apiFetch(`${API}/secrets/hsm/status`),
        apiFetch(`${API}/secrets/pii-log`),
      ]);
      setSecrets(await secretsRes.json());
      setGovernance(await govRes.json());
      setAuditLog(await auditRes.json());
      setHsmStatus(await hsmRes.json());
      setPiiLog(await piiRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load secrets vault');
    } finally {
      setLoading(false);
    }
  }, [keycloak.token]);

  useEffect(() => { fetch_(); }, [fetch_]);

  const createSecret = useCallback(async (payload: CreateSecretPayload): Promise<Secret> => {
    const res = await apiFetch(`${API}/secrets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const created: Secret = await res.json();
    setSecrets(prev => [created, ...prev]);
    await fetch_();
    return created;
  }, [fetch_]);

  const deleteSecret = useCallback(async (id: string) => {
    await apiFetch(`${API}/secrets/${id}`, { method: 'DELETE' });
    setSecrets(prev => prev.map(s => s.id === id ? { ...s, is_active: false } : s));
    await fetch_();
  }, [fetch_]);

  const rotateSecret = useCallback(async (id: string, newValue: string): Promise<Secret> => {
    const res = await apiFetch(`${API}/secrets/${id}/rotate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: newValue }),
    });
    const updated: Secret = await res.json();
    await fetch_();
    return updated;
  }, [fetch_]);

  const revealSecret = useCallback(async (id: string, accessReason?: string): Promise<string> => {
    const url = accessReason
      ? `${API}/secrets/${id}/reveal?access_reason=${encodeURIComponent(accessReason)}`
      : `${API}/secrets/${id}/reveal`;
    const res = await apiFetch(url);
    const data = await res.json();
    return data.value as string;
  }, []);

  const signData = useCallback(async (id: string, data: string) => {
    const res = await apiFetch(`${API}/secrets/${id}/sign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    });
    return res.json() as Promise<{ signature: string; key_version: number }>;
  }, []);

  const verifyData = useCallback(async (id: string, data: string, signature: string) => {
    const res = await apiFetch(`${API}/secrets/${id}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data, signature }),
    });
    return res.json() as Promise<{ valid: boolean }>;
  }, []);

  const updateGovernance = useCallback(async (patch: Partial<GovernanceConfig>) => {
    const res = await apiFetch(`${API}/secrets/governance`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    setGovernance(await res.json());
  }, []);

  const triggerPanic = useCallback(async () => {
    const res = await apiFetch(`${API}/secrets/panic`, { method: 'POST' });
    const result = await res.json();
    await fetch_();
    return result as { revoked: string[]; count: number };
  }, [fetch_]);

  return {
    secrets, governance, auditLog, piiLog, hsmStatus, loading, error,
    createSecret, deleteSecret, rotateSecret, revealSecret,
    signData, verifyData,
    updateGovernance, triggerPanic, refetch: fetch_,
  };
};
