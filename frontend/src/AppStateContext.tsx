import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
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

/* ─── URL route helpers ────────────────────────────────────────────── */
/* Module ids of earlier IAs → the six v3.1 areas (Trang chủ · Cần bạn · Agents · Phân tích ·
 * Dữ liệu · Quản trị). Applied in parseRoute + navigate so existing links and bookmarks keep
 * working and the URL stays canonical. `subs` maps an old page to its new one; a missing
 * entry lands on `sub`, or on the area's first page when `sub` is empty. */
type Redirect = { module: string; sub: string; subs?: Record<string, { module: string; sub: string }> };

const REDIRECT_MAP: Record<string, Redirect> = {
  // V3.0 IA (ask / knowledge / build / ops / settings)
  ask:       { module: 'analysis', sub: 'ASK' },
  knowledge: { module: 'data', sub: 'SOURCES' },
  build: {
    module: 'agents', sub: 'TEAM',
    subs: {
      AGENTS:     { module: 'agents', sub: 'TEAM' },
      WORKFLOWS:  { module: 'agents', sub: 'ORCHESTRATION' },
      PLAYGROUND: { module: 'agents', sub: 'EVALUATE' },
      KNOWLEDGE:  { module: 'data', sub: 'SOURCES' },
      VERSIONS:   { module: 'agents', sub: 'TEAM' },
    },
  },
  ops: {
    module: 'analysis', sub: 'SESSIONS',
    subs: {
      CONVERSATIONS: { module: 'analysis', sub: 'SESSIONS' },
      TRACES:        { module: 'agents', sub: 'RUNS' },
      REVIEWS:       { module: 'needs-you', sub: '' },
      ANALYTICS:     { module: 'analysis', sub: 'SESSIONS' },
    },
  },
  settings: {
    module: 'admin', sub: '',
    subs: {
      TEAM:           { module: 'admin', sub: 'USERS' },
      IAM:            { module: 'admin', sub: 'USERS' },
      PROVIDERS:      { module: 'admin', sub: 'MODELS' },
      AI_PROVIDERS:   { module: 'admin', sub: 'MODELS' },
      API:            { module: 'admin', sub: 'INTEGRATIONS' },
      INFRASTRUCTURE: { module: 'admin', sub: 'MONITORING' },
      AUDIT:          { module: 'admin', sub: 'AUDIT' },
      SECURITY:       { module: 'admin', sub: 'AUDIT' },
      BILLING:        { module: 'agents', sub: 'OPERATIONS' },
    },
  },
  // Older IAs
  'dashboard':          { module: 'analysis', sub: 'SESSIONS' },
  'national-ops':       { module: 'analysis', sub: 'SESSIONS' },
  'knowledge-ops':      { module: 'data', sub: 'SOURCES' },
  'ai-runtime':         { module: 'agents', sub: 'TEAM' },
  'release-management': { module: 'agents', sub: 'TEAM' },
  'release':            { module: 'agents', sub: 'TEAM' },
  'operations-center':  { module: 'agents', sub: 'RUNS' },
  'governance':         { module: 'admin', sub: 'AUDIT' },
  'test':               { module: 'agents', sub: 'EVALUATE' },
  'monitor': {
    module: 'analysis', sub: 'SESSIONS',
    subs: {
      TRACES:    { module: 'agents', sub: 'RUNS' },
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
// /build/WORKFLOWS. parseRoute upper-cases whatever it reads back so both
// case forms of a link (including ones bookmarked before this change) resolve.
const routePath = (module: string, sub?: string, id?: string) =>
  `/${module}${sub ? `/${sub.toLowerCase()}` : ''}${sub && id ? `/${encodeURIComponent(id)}` : ''}`;

const parseRoute = (fallback: { module: string; sub?: string }): { module: string; sub?: string; id?: string } => {
  if (typeof window === 'undefined') return fallback;
  const legacyHash = window.location.hash.replace(/^#\/?/, '');
  const raw = legacyHash || window.location.pathname.replace(/^\/+/, '');
  if (!raw) return fallback;
  const [module, sub, id] = raw.split('/');
  const resolved = resolveRoute(module || fallback.module, sub ? sub.toUpperCase() : undefined);
  // Ids keep their case; they only survive when the page is not redirected elsewhere.
  return id && resolved.sub === sub?.toUpperCase() ? { ...resolved, id: decodeURIComponent(id) } : resolved;
};

const writeRoute = (module: string, sub?: string, id?: string) => {
  if (typeof window === 'undefined') return;
  const next = routePath(module, sub, id);
  if (window.location.pathname !== next || window.location.hash) {
    // Preserve query params so Keycloak can read ?code=&state= on auth redirect.
    // A new page is a history entry, so Back returns from a detail page to its list;
    // rewriting a legacy hash (or the first URL) replaces instead.
    const replace = window.location.hash !== '' || !window.history.state?.app;
    window.history[replace ? 'replaceState' : 'pushState']({ app: true }, '', `${next}${window.location.search}`);
  }
};

export const AppStateProvider = ({ children }: { children: ReactNode }) => {
  const [dataSpace, setDataSpaceState] = useState<DataSpaceRef | null>(null);
  const setDataSpace = useCallback((space: DataSpaceRef | null) => {
    // Every v1 call reads the header from the client, so it changes with the selection.
    setV1DataSpace(space?.id ?? null);
    setDataSpaceState(space);
  }, []);

  const { user: authUser, me } = useAuth();
  const user: User = {
    id:    me?.id ?? authUser?.id ?? 'u_anon',
    name:  me?.display_name ?? authUser?.name ?? authUser?.email ?? 'User',
    email: me?.email ?? authUser?.email ?? '',
  };

  /* Routing state — initialized from URL hash, else by role */
  const initial = parseRoute(landingFor());
  const [activeModule, _setActiveModule] = useState<string>(initial.module);
  const [subTab, _setSubTabState] = useState<Record<string, string>>(
    initial.sub ? { [initial.module]: initial.sub } : {},
  );
  const [routeId, setRouteId] = useState<string | undefined>(initial.id);

  const setActiveModule = useCallback((id: string) => {
    _setActiveModule(id);
  }, []);

  const setSubTab = useCallback((moduleId: string, subId: string) => {
    setRouteId(undefined);
    _setSubTabState(prev => ({ ...prev, [moduleId]: subId }));
  }, []);

  const getSubTab = useCallback(
    (moduleId: string, fallback?: string) => subTab[moduleId] ?? fallback,
    [subTab],
  );

  const navigate = useCallback((moduleId: string, subId?: string, id?: string) => {
    /* Honor redirect map if a caller still uses an old module id */
    const resolved = resolveRoute(moduleId, subId);
    moduleId = resolved.module;
    subId = resolved.sub;
    _setActiveModule(moduleId);
    setRouteId(subId ? id : undefined);
    // No page means the area's own start (its landing, or its first rail page).
    const page = subId;
    _setSubTabState(prev => {
      const next = { ...prev };
      if (page) next[moduleId] = page;
      else delete next[moduleId];
      return next;
    });
  }, []);

  /* Keep URL path in sync with state */
  useEffect(() => {
    writeRoute(activeModule, subTab[activeModule], routeId);
  }, [activeModule, subTab, routeId]);

  /* Listen for back/forward navigation */
  useEffect(() => {
    const onRouteChange = () => {
      const { module, sub, id } = parseRoute(landingFor());
      _setActiveModule(module);
      setRouteId(id);
      _setSubTabState(prev => {
        const next = { ...prev };
        if (sub) next[module] = sub;
        else delete next[module];
        return next;
      });
    };
    window.addEventListener('popstate', onRouteChange);
    window.addEventListener('hashchange', onRouteChange);
    return () => {
      window.removeEventListener('popstate', onRouteChange);
      window.removeEventListener('hashchange', onRouteChange);
    };
  }, []);

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
