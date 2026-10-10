import keycloak from './keycloak';

const API = import.meta.env.VITE_API_URL ?? '';

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
