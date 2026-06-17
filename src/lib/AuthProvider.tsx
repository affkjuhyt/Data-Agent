import React, { createContext, useContext, useEffect, useState } from 'react';
import keycloak from './keycloak';

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
  const [user, setUser]       = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    // No mock/demo bypass: the app always authenticates against Keycloak and
    // talks to the backend through Kong. There is no offline/demo mode.
    if ((keycloak as any).didInitialize) {
      setUser(buildUser());
      setLoading(false);
      return;
    }

    keycloak
      .init({
        onLoad:           'login-required',
        checkLoginIframe: false,
        pkceMethod:       'S256',
      })
      .then(authenticated => {
        if (authenticated) {
          setUser(buildUser());
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

  return (
    <Ctx.Provider value={{ user, loading, logout }}>
      {children}
    </Ctx.Provider>
  );
};
