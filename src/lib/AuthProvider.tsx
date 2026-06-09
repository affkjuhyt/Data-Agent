import React, { createContext, useContext, useEffect, useState } from 'react';
import keycloak from './keycloak';
import { fetchMyStatus, submitProfile } from './useMicrosoftApprovals';
import { PendingApproval } from '../components/PendingApproval';
import { ProfileSetup } from '../components/ProfileSetup';

interface AuthUser {
  id:    string;
  email: string;
  name:  string;
  roles: string[];
  token: string;
}

interface AuthCtx {
  user:    AuthUser | null;
  loading: boolean;
  logout:  () => void;
}

type ApprovalGateStatus = 'checking' | 'active' | 'needs_profile' | 'pending_approval' | 'rejected';

const Ctx = createContext<AuthCtx>({ user: null, loading: true, logout: () => {} });

export const useAuth = () => useContext(Ctx);

const buildUser = (): AuthUser | null => {
  if (!keycloak.authenticated || !keycloak.tokenParsed) return null;
  const p = keycloak.tokenParsed as any;
  return {
    id:    p.sub,
    email: p.email ?? '',
    name:  p.name ?? p.preferred_username ?? '',
    roles: p.realm_access?.roles ?? [],
    token: keycloak.token ?? '',
  };
};

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser]               = useState<AuthUser | null>(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [gateStatus, setGateStatus]   = useState<ApprovalGateStatus>('checking');
  const [gateSince, setGateSince]     = useState<string | undefined>(undefined);

  const checkApprovalGate = async () => {
    try {
      const { status, since } = await fetchMyStatus();
      setGateSince(since);
      setGateStatus(status as ApprovalGateStatus);
    } catch {
      setGateStatus('active');
    }
  };

  useEffect(() => {
    // Dev bypass: VITE_MOCK_AUTH=true trong .env.local → bỏ qua Keycloak
    if (import.meta.env.VITE_MOCK_AUTH === 'true') {
      setUser({ id: 'u_123', name: 'Alex Rivera', email: 'alex.rivera@globalcorp.ai', roles: ['AI_ENGINEER'], token: 'mock-token' });
      setGateStatus('active');
      setLoading(false);
      return;
    }

    if ((keycloak as any).didInitialize) {
      const u = buildUser();
      setUser(u);
      if (u) checkApprovalGate().finally(() => setLoading(false));
      else setLoading(false);
      return;
    }

    keycloak
      .init({
        onLoad:           'login-required',
        checkLoginIframe: false,
        pkceMethod:       'S256',
      })
      .then(async authenticated => {
        if (authenticated) {
          const u = buildUser();
          setUser(u);
          if (u) await checkApprovalGate();
        } else {
          keycloak?.login();
        }
      })
      .catch(err => {
        const msg = err instanceof Error ? err.message : JSON.stringify(err);
        console.error('Keycloak init failed:', err);
        setError(msg || 'Cannot reach authentication server.');
      })
      .finally(() => setLoading(false));

    keycloak.onTokenExpired = () =>
      keycloak.updateToken(30).catch(() => keycloak.login());
  }, []);

  const logout = () => keycloak.logout({ redirectUri: window.location.origin });

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-[#FAF6ED]">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-[#B88719] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-[#5A5A5A]">Connecting to Keycloak…</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-[#FAF6ED]">
        <div className="flex flex-col items-center gap-4 max-w-sm text-center p-6">
          <div className="text-2xl">⚠️</div>
          <p className="text-sm text-[#5A5A5A] font-mono break-all">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 bg-[#B88719] text-white rounded-lg text-sm"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!user) return null;

  if (gateStatus === 'needs_profile') {
    return (
      <ProfileSetup
        email={user.email}
        onSubmit={async (role, tenantId) => {
          await submitProfile(role, tenantId);
          await checkApprovalGate();
        }}
      />
    );
  }

  if (gateStatus === 'pending_approval') {
    return (
      <PendingApproval
        email={user.email}
        since={gateSince}
        onRetry={() => {
          setGateStatus('checking');
          checkApprovalGate();
        }}
      />
    );
  }

  if (gateStatus === 'rejected') {
    return (
      <div className="h-screen flex items-center justify-center bg-[#FAF6ED]">
        <div className="flex flex-col items-center gap-4 max-w-sm text-center p-6">
          <div className="text-2xl">🚫</div>
          <p className="text-sm font-semibold text-[#111]">Access denied</p>
          <p className="text-sm text-[#5A5A5A]">
            Your Microsoft account access request was rejected by an administrator.
          </p>
          <button
            onClick={() => keycloak.logout({ redirectUri: window.location.origin })}
            className="px-4 py-2 bg-[#111111] text-white rounded-lg text-sm"
          >
            Sign out
          </button>
        </div>
      </div>
    );
  }

  return (
    <Ctx.Provider value={{ user, loading, logout }}>
      {children}
    </Ctx.Provider>
  );
};
