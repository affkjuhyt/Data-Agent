import { useState, useEffect, useCallback } from 'react';
import keycloak from './keycloak';

const KC_URL   = import.meta.env.VITE_KEYCLOAK_URL   ?? 'http://localhost:8110';
const KC_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow';

export interface KcUser {
  id: string;
  username: string;
  email: string;
  firstName?: string;
  lastName?: string;
  enabled: boolean;
  emailVerified: boolean;
  createdTimestamp: number;
}

interface Result {
  users: KcUser[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export const useKeycloakUsers = (isAdmin: boolean): Result => {
  const [users, setUsers]     = useState<KcUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    if (!isAdmin || !keycloak.token) return;
    setLoading(true);
    setError(null);
    try {
      await keycloak.updateToken(30).catch(() => {});

      const p = keycloak.tokenParsed as any;
      const tenantId = p?.tenant_id ?? '';
      const qs = tenantId
        ? `q=tenant_id%3A${encodeURIComponent(tenantId)}&max=200`
        : 'max=200';

      const res = await fetch(
        `${KC_URL}/admin/realms/${KC_REALM}/users?${qs}`,
        { headers: { Authorization: `Bearer ${keycloak.token}` } },
      );
      if (res.status === 403) throw new Error('Token lacks view-users permission — assign realm-management → view-users in Keycloak');
      if (!res.ok) throw new Error(`Keycloak Admin API ${res.status}`);
      setUsers(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch users');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, keycloak.token]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  useEffect(() => {
    const handler = () => fetchUsers();
    window.addEventListener('sso-approval-changed', handler);
    return () => window.removeEventListener('sso-approval-changed', handler);
  }, [fetchUsers]);

  return { users, loading, error, refetch: fetchUsers };
};
