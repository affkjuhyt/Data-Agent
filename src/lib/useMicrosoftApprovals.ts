import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const API = import.meta.env.VITE_API_URL ?? '';

export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

export interface MsApproval {
  id: string;
  keycloak_user_id: string;
  email: string;
  display_name: string;
  status: ApprovalStatus;
  requested_role: string | null;
  requested_tenant_id: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

interface Result {
  approvals: MsApproval[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
  approve: (userId: string) => Promise<void>;
  reject:  (userId: string) => Promise<void>;
}

async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  await keycloak.updateToken(30).catch(() => {});
  return fetch(`${API}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${keycloak.token}`,
      ...(init?.headers ?? {}),
    },
  });
}

export const useMicrosoftApprovals = (
  isAdmin: boolean,
  status: ApprovalStatus | 'all' = 'pending',
): Result => {
  const [approvals, setApprovals] = useState<MsApproval[]>([]);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const fetchApprovals = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/auth/approvals?status=${status}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setApprovals(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch approvals');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, status]);

  useEffect(() => { fetchApprovals(); }, [fetchApprovals]);

  const approve = useCallback(async (userId: string) => {
    const res = await authFetch(`/api/auth/approvals/${userId}/approve`, { method: 'POST' });
    if (!res.ok) throw new Error(`Approve failed: HTTP ${res.status}`);
    await fetchApprovals();
  }, [fetchApprovals]);

  const reject = useCallback(async (userId: string) => {
    const res = await authFetch(`/api/auth/approvals/${userId}/reject`, { method: 'POST' });
    if (!res.ok) throw new Error(`Reject failed: HTTP ${res.status}`);
    await fetchApprovals();
  }, [fetchApprovals]);

  return { approvals, loading, error, refetch: fetchApprovals, approve, reject };
};

export async function fetchMyStatus(): Promise<{ status: string; since?: string }> {
  await keycloak.updateToken(30).catch(() => {});
  const res = await fetch(`${API}/api/auth/me`, {
    headers: { Authorization: `Bearer ${keycloak.token}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function submitProfile(requestedRole: string, requestedTenantId: string): Promise<{ status: string; since?: string }> {
  await keycloak.updateToken(30).catch(() => {});
  const res = await fetch(`${API}/api/auth/me/profile`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${keycloak.token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ requested_role: requestedRole, requested_tenant_id: requestedTenantId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchAvailableRoles(): Promise<string[]> {
  await keycloak.updateToken(30).catch(() => {});
  const res = await fetch(`${API}/api/auth/me/roles`, {
    headers: { Authorization: `Bearer ${keycloak.token}` },
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.roles ?? [];
}

export interface TenantOption { id: string; name: string; slug: string; }

export async function fetchAvailableTenants(): Promise<TenantOption[]> {
  await keycloak.updateToken(30).catch(() => {});
  const res = await fetch(`${API}/api/auth/me/tenants`, {
    headers: { Authorization: `Bearer ${keycloak.token}` },
  });
  if (!res.ok) return [];
  return res.json();
}
