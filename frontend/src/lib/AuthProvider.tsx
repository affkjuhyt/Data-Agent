import React, { createContext, useContext, useEffect, useState } from 'react';
import keycloak from './keycloak';
import { V1Error } from './v1/client';
import { getMe, type Me } from './v1/me';
import { AuthScreen } from '../features/auth/AuthScreen';
import { LoginPage } from '../features/auth/LoginPage';
import { NoAccount } from '../features/auth/NoAccount';
import { Button } from '../design-system/Button';
import { Notice } from '../design-system/Notice';

interface AuthUser {
  id:    string;
  email: string;
  name:  string;
  token: string;
}

interface AuthCtx {
  user:    AuthUser | null;
  /** The user's account in the organisation (GET /v1/me); null before it loads. */
  me:      Me | null;
  loading: boolean;
  logout:  () => void;
}

type AccountState = 'checking' | 'active' | 'no_account';

const Ctx = createContext<AuthCtx>({ user: null, me: null, loading: true, logout: () => {} });

export const useAuth = () => useContext(Ctx);

const buildUser = (): AuthUser | null => {
  if (!keycloak.authenticated || !keycloak.tokenParsed) return null;
  const p = keycloak.tokenParsed as any;
  return {
    id:    p.sub,
    email: p.email ?? '',
    name:  p.name ?? p.preferred_username ?? '',
    token: keycloak.token ?? '',
  };
};

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [user, setUser]               = useState<AuthUser | null>(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState<string | null>(null);
  const [account, setAccount]         = useState<AccountState>('checking');
  const [me, setMe]                   = useState<Me | null>(null);

  // Roles and capabilities live in org.* and change without a re-login, so they come from the
  // API, never from the token. 403 is "no active account here"; anything else is an outage the
  // user can retry, never a reason to let them in without capabilities.
  const loadAccount = async () => {
    setAccount('checking');
    try {
      setMe(await getMe());
      setAccount('active');
      setError(null);
    } catch (err) {
      if (err instanceof V1Error && err.status === 403) {
        setAccount('no_account');
        return;
      }
      setError(err instanceof Error ? err.message : 'Không tải được tài khoản.');
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
      if (u) loadAccount().finally(() => setLoading(false));
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
          if (u) await loadAccount();
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

  if (account === 'checking') {
    return (
      <AuthScreen icon="lock" title="Đang đăng nhập…">
        <p className="text-center text-muted-foreground da-body">Đang tải tài khoản của bạn.</p>
      </AuthScreen>
    );
  }

  if (account === 'no_account') return <NoAccount email={user.email} onRetry={loadAccount} />;

  return (
    <Ctx.Provider value={{ user, me, loading, logout }}>
      {children}
    </Ctx.Provider>
  );
};
