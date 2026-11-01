import { useNavigate, useRouterState } from '@tanstack/react-router';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { DataSpaceRef, User } from './types';
import { useAuth } from './lib/AuthProvider';
import { routePath } from './routePath';
import { setDataSpace as setV1DataSpace } from './lib/v1/client';

interface AppStateContextType {
  /** The Data Space chosen in the top bar; null = Toàn tổ chức (no X-Data-Space, A1.2). */
  dataSpace: DataSpaceRef | null;
  setDataSpace: (space: DataSpaceRef | null) => void;
  user: User;

  /* Navigation routing — single source of truth */
  activeModule: string;
  setActiveModule: (id: string) => void;
  subTab: Record<string, string>;
  setSubTab: (moduleId: string, subId: string) => void;
  getSubTab: (moduleId: string, fallback?: string) => string | undefined;

  /* Navigation imperatives */
  navigate: (moduleId: string, subId?: string, id?: string) => void;
  /** The record a detail page shows: the third URL segment, /needs-you/proposal/<id>. */
  routeId: string | undefined;
}

const AppStateContext = createContext<AppStateContextType | undefined>(undefined);

/** Keep the top-bar selection stable across a full page reload. */
export const DATA_SPACE_STORAGE_KEY = 'dataagent.data-space';
export const ORGANISATION_SCOPE = '__organisation__';

/* ─── URL route helpers ────────────────────────────────────────────── */
/** Everyone lands on Trang chủ (v3.1): it is the start of every role's day. */
export const landingFor = (): { module: string; sub?: string } => ({ module: 'home' });

/** The route a pathname names, after the router has made it canonical. */
const routeOf = (pathname: string): { module: string; sub?: string; id?: string } => {
  const [module, sub, id] = pathname.replace(/^\/+/, '').split('/');
  return {
    module: module || landingFor().module,
    sub: sub ? sub.toUpperCase() : undefined,
    id: sub && id ? decodeURIComponent(id) : undefined,
  };
};

export const AppStateProvider = ({ children }: { children: ReactNode }) => {
  const [dataSpace, setDataSpaceState] = useState<DataSpaceRef | null>(null);
  const setDataSpace = useCallback((space: DataSpaceRef | null) => {
    // Every v1 call reads the header from the client, so it changes with the selection.
    setV1DataSpace(space?.id ?? null);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(DATA_SPACE_STORAGE_KEY, space?.id ?? ORGANISATION_SCOPE);
    }
    setDataSpaceState(space);
  }, []);

  const { user: authUser, me } = useAuth();
  const user: User = {
    id: me?.id ?? authUser?.id ?? 'u_anon',
    name: me?.display_name ?? authUser?.name ?? authUser?.email ?? 'User',
    email: me?.email ?? authUser?.email ?? '',
  };

  /* Routing state: the router's location is the single source of truth (router.tsx). */
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const routerNavigate = useNavigate();
  const current = useMemo(() => routeOf(pathname), [pathname]);
  const activeModule = current.module;
  const routeId = current.id;
  // The page last open in each area, so returning to an area can reopen it (getSubTab).
  const [remembered, setRemembered] = useState<Record<string, string>>({});
  // A page opened by a link (AppLink) is remembered too, not only one opened by navigate().
  useEffect(() => {
    const { module, sub } = current;
    if (sub) setRemembered((prev) => (prev[module] === sub ? prev : { ...prev, [module]: sub }));
  }, [current]);
  const subTab = useMemo(() => {
    const next = { ...remembered };
    if (current.sub) next[current.module] = current.sub;
    else delete next[current.module];
    return next;
  }, [remembered, current.module, current.sub]);

  const navigate = useCallback(
    (moduleId: string, subId?: string, id?: string) => {
      const resolved = { module: moduleId, sub: subId };
      setRemembered((prev) => {
        const next = { ...prev };
        if (resolved.sub) next[resolved.module] = resolved.sub;
        else delete next[resolved.module];
        return next;
      });
      // A new page is a history entry, so Back returns from a detail page to its list. The query
      // string stays: Keycloak reads ?code=&state= from it on its redirect back.
      void routerNavigate({
        href: `${routePath(resolved.module, resolved.sub, resolved.sub ? id : undefined)}${window.location.search}`,
      });
    },
    [routerNavigate],
  );

  const setActiveModule = useCallback((id: string) => navigate(id, subTab[id]), [navigate, subTab]);

  const setSubTab = useCallback(
    (moduleId: string, subId: string) => {
      if (moduleId === activeModule) navigate(moduleId, subId);
      else setRemembered((prev) => ({ ...prev, [moduleId]: subId }));
    },
    [activeModule, navigate],
  );

  const getSubTab = useCallback(
    (moduleId: string, fallback?: string) => subTab[moduleId] ?? fallback,
    [subTab],
  );

  return (
    <AppStateContext.Provider
      value={{
        dataSpace,
        setDataSpace,
        user,
        activeModule,
        setActiveModule,
        subTab,
        setSubTab,
        getSubTab,
        navigate,
        routeId,
      }}
    >
      {children}
    </AppStateContext.Provider>
  );
};

export const useAppState = () => {
  const context = useContext(AppStateContext);
  if (!context) throw new Error('useAppState must be used within AppStateProvider');
  return context;
};
