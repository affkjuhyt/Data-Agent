import React, { createContext, useContext, useEffect, useState } from 'react';
import keycloak from './keycloak';
import { fetchMyStatus, submitProfile } from './useMicrosoftApprovals';
import { AuthScreen } from '../features/auth/AuthScreen';
import { LoginPage } from '../features/auth/LoginPage';
import { PendingApproval } from '../features/auth/PendingApproval';
import { ProfileSetup } from '../features/auth/ProfileSetup';
import { Button } from '../design-system/Button';
import { Notice } from '../design-system/Notice';

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
    } catch (err) {
      // The Microsoft-SSO approval API (auth_api, /api/auth/me) is optional: it is
      // not part of the KB-only stacks (auth-lite / e2e), where Kong routes only
      // /api/data, /api/knowledge, /api/fleet. If it is unreachable, fail OPEN —
      // skip the approval gate rather than locking the whole app behind an error
      // screen. When auth_api IS deployed and routed, the gate works as designed.
      console.warn('Approval gate unavailable — skipping (auth_api not deployed?):', err);
      setGateStatus('active');
    }
  };

  useEffect(() => {
    // No mock/demo bypass: the app always authenticates against Keycloak and
    // talks to the backend through Kong. There is no offline/demo mode.
    // check-sso, not login-required: an anonymous visit gets the app's own login page
    // (Figma "v3.1 · Đăng nhập"), which sends the user to Keycloak or straight to the IdP.
    if ((keycloak as any).didInitialize) {
      const u = buildUser();
      setUser(u);
      if (u) checkApprovalGate().finally(() => setLoading(false));
      else setLoading(false);
      return;
    }

    keycloak
      .init({
        onLoad:           'check-sso',
        silentCheckSsoRedirectUri: `${window.location.origin}/silent-check-sso.html`,
        checkLoginIframe: false,
        pkceMethod:       'S256',
      })
      .then(async authenticated => {
        if (authenticated) {
          const u = buildUser();
          setUser(u);
          if (u) await checkApprovalGate();
        }
      })
      .catch(err => {
        const msg = err instanceof Error ? err.message : JSON.stringify(err);
        console.error('Keycloak init failed:', err);
        setError(msg || 'Không kết nối được máy chủ xác thực.');
      })
      .finally(() => setLoading(false));

    keycloak.onTokenExpired = () =>
      keycloak.updateToken(30).catch(() => keycloak.login());
  }, []);

  const logout = () => keycloak.logout({ redirectUri: window.location.origin });

  if (loading) {
    return (
      <AuthScreen icon="lock" title="Đang đăng nhập…">
        <p className="text-center text-muted-foreground da-body">Đang kết nối tới máy chủ xác thực.</p>
      </AuthScreen>
    );
  }

  if (error) {
    return (
      <AuthScreen icon="alert" title="Không đăng nhập được">
        <Notice tone="error">{error}</Notice>
        <Button icon="refresh" onClick={() => window.location.reload()}>
          Thử lại
        </Button>
      </AuthScreen>
    );
  }

  if (!user) return <LoginPage />;

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
      <AuthScreen icon="lock" title="Không được cấp quyền">
        <p className="text-center text-muted-foreground da-body">
          Quản trị đã từ chối yêu cầu truy cập của tài khoản Microsoft này.
        </p>
        <Button kind="secondary" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}>
          Đăng xuất
        </Button>
      </AuthScreen>
    );
  }

  return (
    <Ctx.Provider value={{ user, loading, logout }}>
      {children}
    </Ctx.Provider>
  );
};
