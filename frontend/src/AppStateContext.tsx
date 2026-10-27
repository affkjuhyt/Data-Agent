import { useNavigate, useRouterState } from '@tanstack/react-router';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { DataSpaceRef, User } from './types';
import { useAuth } from './lib/AuthProvider';
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
/* Module ids of earlier IAs → the six v3.1 areas (Trang chủ · Cần bạn · Agents · Phân tích ·
 * Dữ liệu · Quản trị). Applied by the routes (router.tsx) and navigate so existing links and bookmarks keep
 * working and the URL stays canonical. `subs` maps an old page to its new one; a missing
 * entry lands on `sub`, or on the area's first page when `sub` is empty. */
type Redirect = {
  module: string;
  sub: string;
  subs?: Record<string, { module: string; sub: string }>;
};

const REDIRECT_MAP: Record<string, Redirect> = {
  // V3.0 IA (ask / knowledge / build / ops / settings)
  ask: { module: 'analysis', sub: 'ASK' },
  knowledge: { module: 'data', sub: 'SOURCES' },
  build: {
    module: 'agents',
    sub: 'TEAM',
    subs: {
      AGENTS: { module: 'agents', sub: 'TEAM' },
      WORKFLOWS: { module: 'agents', sub: 'ORCHESTRATION' },
      PLAYGROUND: { module: 'agents', sub: 'EVALUATE' },
      KNOWLEDGE: { module: 'data', sub: 'SOURCES' },
      VERSIONS: { module: 'agents', sub: 'TEAM' },
    },
  },
  ops: {
    module: 'analysis',
    sub: 'SESSIONS',
    subs: {
      CONVERSATIONS: { module: 'analysis', sub: 'SESSIONS' },
      TRACES: { module: 'agents', sub: 'RUNS' },
      REVIEWS: { module: 'needs-you', sub: '' },
      ANALYTICS: { module: 'analysis', sub: 'SESSIONS' },
    },
  },
  settings: {
    module: 'admin',
    sub: '',
    subs: {
      TEAM: { module: 'admin', sub: 'USERS' },
      IAM: { module: 'admin', sub: 'USERS' },
      PROVIDERS: { module: 'admin', sub: 'MODELS' },
      AI_PROVIDERS: { module: 'admin', sub: 'MODELS' },
      API: { module: 'admin', sub: 'INTEGRATIONS' },
      INFRASTRUCTURE: { module: 'admin', sub: 'MONITORING' },
      AUDIT: { module: 'admin', sub: 'AUDIT' },
      SECURITY: { module: 'admin', sub: 'AUDIT' },
      BILLING: { module: 'agents', sub: 'OPERATIONS' },
    },
  },
  // Older IAs
  dashboard: { module: 'analysis', sub: 'SESSIONS' },
  'national-ops': { module: 'analysis', sub: 'SESSIONS' },
  'knowledge-ops': { module: 'data', sub: 'SOURCES' },
  'ai-runtime': { module: 'agents', sub: 'TEAM' },
  'release-management': { module: 'agents', sub: 'TEAM' },
  release: { module: 'agents', sub: 'TEAM' },
  'operations-center': { module: 'agents', sub: 'RUNS' },
  governance: { module: 'admin', sub: 'AUDIT' },
  test: { module: 'agents', sub: 'EVALUATE' },
  monitor: {
    module: 'analysis',
    sub: 'SESSIONS',
    subs: {
      TRACES: { module: 'agents', sub: 'RUNS' },
      ANALYTICS: { module: 'analysis', sub: 'SESSIONS' },
    },
  },
};

/** Everyone lands on Trang chủ (v3.1): it is the start of every role's day. */
export const landingFor = (): { module: string; sub?: string } => ({ module: 'home' });

export const resolveRoute = (module: string, sub?: string): { module: string; sub?: string } => {
  const r = REDIRECT_MAP[module];
  if (!r) return { module, sub };
  const mapped = sub ? r.subs?.[sub] : undefined;
  if (mapped) return { module: mapped.module, sub: mapped.sub || undefined };
  return { module: r.module, sub: r.sub || undefined };
};

// Sub-tab keys are uppercase internally (ROUTE_MAP, REDIRECT_MAP, ...) but the
// URL renders them lowercase — /build/workflows reads better than
// /build/WORKFLOWS. The router (router.tsx) redirects an upper-case page to this form.
export const routePath = (module: string, sub?: string, id?: string) =>
  `/${module}${sub ? `/${sub.toLowerCase()}` : ''}${sub && id ? `/${encodeURIComponent(id)}` : ''}`;

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
  const subTab = useMemo(() => {
    const next = { ...remembered };
    if (current.sub) next[current.module] = current.sub;
    else delete next[current.module];
    return next;
  }, [remembered, current.module, current.sub]);

  const navigate = useCallback(
    (moduleId: string, subId?: string, id?: string) => {
      /* Honor redirect map if a caller still uses an old module id */
      const resolved = resolveRoute(moduleId, subId);
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
