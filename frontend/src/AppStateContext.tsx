import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { Role, Industry, TenantContext, User, Environment, KnowledgeDocument, PagedResponse } from './types';
import { apiGet } from './lib/apiClient';
import { useAuth } from './lib/AuthProvider';
import { pickTenant, type Tenant } from './lib/platformApi';

interface AppStateContextType {
  role: Role;
  setRole: (role: Role) => void;
  industry: Industry;
  setIndustry: (industry: Industry) => void;
  tenant: TenantContext;
  setTenant: (tenant: Partial<TenantContext>) => void;
  /** Tenants the signed-in user belongs to (GET /api/auth/me/tenants). */
  tenants: Tenant[];
  tenantsLoading: boolean;
  user: User;
  isExpertMode: boolean;
  setIsExpertMode: (val: boolean) => void;

  /* Navigation routing — single source of truth */
  activeModule: string;
  setActiveModule: (id: string) => void;
  subTab: Record<string, string>;
  setSubTab: (moduleId: string, subId: string) => void;
  getSubTab: (moduleId: string, fallback?: string) => string | undefined;

  /* Navigation imperatives */
  navigate: (moduleId: string, subId?: string) => void;

  /* Documents — loaded on mount, mutated in-place */
  documents: KnowledgeDocument[];
  docsLoading: boolean;
  addDocument: (doc: KnowledgeDocument) => void;
  updateDocument: (id: string, patch: Partial<KnowledgeDocument>) => void;
  deleteDocument: (id: string) => void;

  /* Wizard trigger — sidebar action buttons */
  pendingAction: string | null;
  setPendingAction: (action: string | null) => void;
}

const AppStateContext = createContext<AppStateContextType | undefined>(undefined);

/* ─── URL route helpers ────────────────────────────────────────────── */
/* Old module IDs → current IA. Applied in parseRoute + navigate so existing
 * links/bookmarks keep working and the URL stays canonical. The 2026-08 IA
 * change (Build/Test/Release/Monitor → Hỏi dữ liệu/Tri thức/Xây dựng/Vận hành)
 * is expressed here too, with a sub-tab map so "#monitor/TRACES" still lands
 * on the traces screen rather than the module's first item. */
const REDIRECT_MAP: Record<string, { module: string; sub: string; subs?: Record<string, string> }> = {
  'dashboard':           { module: 'ops',      sub: 'ANALYTICS' },
  'knowledge-ops':       { module: 'knowledge', sub: '' },
  'ai-runtime':          { module: 'build',    sub: 'AGENTS' },
  'operations-center':   { module: 'ops',      sub: 'TRACES' },
  'release-management':  { module: 'build',    sub: 'VERSIONS' },
  'governance':          { module: 'settings', sub: 'SECURITY' },
  'national-ops':        { module: 'ops',      sub: 'ANALYTICS' },
  // previous 5-module IA
  'test':                { module: 'build',    sub: 'PLAYGROUND', subs: { PLAYGROUND: 'PLAYGROUND' } },
  'release':             { module: 'build',    sub: 'VERSIONS',   subs: { VERSIONS: 'VERSIONS' } },
  'monitor':             { module: 'ops',      sub: 'CONVERSATIONS', subs: { CONVERSATIONS: 'CONVERSATIONS', TRACES: 'TRACES', ANALYTICS: 'ANALYTICS' } },
};
// "#build/KNOWLEDGE" (old) → the knowledge module
const BUILD_SUB_REDIRECTS: Record<string, { module: string; sub?: string }> = {
  KNOWLEDGE: { module: 'knowledge' },
};
// Settings sub-tabs before the nested sidebar was folded into the module nav.
const SETTINGS_SUB_REDIRECTS: Record<string, string> = {
  TEAM: 'IAM',
  PROVIDERS: 'AI_PROVIDERS',
  AUDIT: 'SECURITY',
};

/** Where a user lands with no hash: business users ask, engineers build. */
export const landingFor = (role: Role): { module: string; sub?: string } =>
  role === 'AI_ENGINEER' || role === 'PLATFORM_ADMIN' ? { module: 'build', sub: 'AGENTS' } : { module: 'ask' };

export const resolveRoute = (module: string, sub?: string): { module: string; sub?: string } => {
  const r = REDIRECT_MAP[module];
  if (r) {
    const mapped = sub && r.subs?.[sub];
    return { module: r.module, sub: mapped || r.sub || undefined };
  }
  if (module === 'build' && sub && BUILD_SUB_REDIRECTS[sub]) return BUILD_SUB_REDIRECTS[sub];
  if (module === 'settings' && sub && SETTINGS_SUB_REDIRECTS[sub]) return { module, sub: SETTINGS_SUB_REDIRECTS[sub] };
  return { module, sub };
};

// Sub-tab keys are uppercase internally (ROUTE_MAP, REDIRECT_MAP, ...) but the
// URL renders them lowercase — /build/workflows reads better than
// /build/WORKFLOWS. parseRoute upper-cases whatever it reads back so both
// case forms of a link (including ones bookmarked before this change) resolve.
const routePath = (module: string, sub?: string) => `/${module}${sub ? `/${sub.toLowerCase()}` : ''}`;

const parseRoute = (fallback: { module: string; sub?: string }): { module: string; sub?: string } => {
  if (typeof window === 'undefined') return fallback;
  const legacyHash = window.location.hash.replace(/^#\/?/, '');
  const raw = legacyHash || window.location.pathname.replace(/^\/+/, '');
  if (!raw) return fallback;
  const [module, sub] = raw.split('/');
  return resolveRoute(module || fallback.module, sub ? sub.toUpperCase() : undefined);
};

const writeRoute = (module: string, sub?: string) => {
  if (typeof window === 'undefined') return;
  const next = routePath(module, sub);
  if (window.location.pathname !== next || window.location.hash) {
    // Preserve query params so Keycloak can read ?code=&state= on auth redirect
    window.history.replaceState(null, '', `${next}${window.location.search}`);
  }
};

/* ─── Tenant persistence ───────────────────────────────────────────── */
const TENANT_KEY = 'app:tenantId';
const ENV_KEY = 'app:environment';
const ENV_VALUES: Environment[] = ['DEV', 'UAT', 'STAGING', 'PROD'];

const readStorage = (key: string): string | null => {
  try { return typeof window !== 'undefined' ? window.localStorage.getItem(key) : null; } catch { return null; }
};
const writeStorage = (key: string, value: string | null) => {
  try {
    if (typeof window === 'undefined') return;
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch { /* storage unavailable — selection is session-only */ }
};

/** Environment pill choice survives reloads; defaults to PROD. */
export const initialEnvironment = (stored: string | null): Environment =>
  ENV_VALUES.includes(stored as Environment) ? (stored as Environment) : 'PROD';

export const AppStateProvider = ({ children }: { children: ReactNode }) => {
  const [role, setRole] = useState<Role>('AI_ENGINEER');
  const [industry, setIndustry] = useState<Industry>('GENERAL');
  const [isExpertMode, setIsExpertMode] = useState(false);
  const [tenant, setTenantState] = useState<TenantContext>(() => ({
    tenantId: null,
    tenantName: null,
    slug: null,
    environment: initialEnvironment(readStorage(ENV_KEY)),
  }));
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantsLoading, setTenantsLoading] = useState(false);

  const { user: authUser } = useAuth();
  const user: User = {
    id:    authUser?.id    ?? 'u_anon',
    name:  authUser?.name  ?? authUser?.email ?? 'User',
    email: authUser?.email ?? '',
    role:  (() => {
      const normalized = authUser?.roles?.map(r => r.toUpperCase().replace(/-/g, '_')) ?? [];
      if (normalized.includes('PLATFORM_ADMIN')) return 'PLATFORM_ADMIN';
      if (normalized.includes('AI_ENGINEER')) return 'AI_ENGINEER';
      if (normalized.includes('EXECUTIVE_VIEWER')) return 'EXECUTIVE';
      return 'AI_ENGINEER';
    })() as Role,
  };

  /* Tenants — loaded once the user is authenticated */
  const authUserId = authUser?.id ?? null;
  useEffect(() => {
    if (!authUserId) return;
    let cancelled = false;
    setTenantsLoading(true);
    apiGet<Tenant[]>('/api/auth/me/tenants')
      .then(list => {
        if (cancelled) return;
        const safe = Array.isArray(list) ? list : [];
        setTenants(safe);
        const chosen = pickTenant(safe, readStorage(TENANT_KEY));
        setTenantState(prev => ({
          ...prev,
          tenantId: chosen?.id ?? null,
          tenantName: chosen?.name ?? null,
          slug: chosen?.slug ?? null,
        }));
      })
      .catch(() => { if (!cancelled) setTenants([]); })
      .finally(() => { if (!cancelled) setTenantsLoading(false); });
    return () => { cancelled = true; };
  }, [authUserId]);

  /* Routing state — initialized from URL hash, else by role */
  const initial = parseRoute(landingFor(user.role));
  const [activeModule, _setActiveModule] = useState<string>(initial.module);
  const [subTab, _setSubTabState] = useState<Record<string, string>>(
    initial.sub ? { [initial.module]: initial.sub } : {},
  );

  const setActiveModule = useCallback((id: string) => {
    _setActiveModule(id);
  }, []);

  const setSubTab = useCallback((moduleId: string, subId: string) => {
    _setSubTabState(prev => ({ ...prev, [moduleId]: subId }));
  }, []);

  const getSubTab = useCallback(
    (moduleId: string, fallback?: string) => subTab[moduleId] ?? fallback,
    [subTab],
  );

  const navigate = useCallback((moduleId: string, subId?: string) => {
    /* Honor redirect map if a caller still uses an old module id */
    const resolved = resolveRoute(moduleId, subId);
    moduleId = resolved.module;
    subId = resolved.sub;
    _setActiveModule(moduleId);
    if (subId) {
      _setSubTabState(prev => ({ ...prev, [moduleId]: subId }));
    }
  }, []);

  /* Keep URL path in sync with state */
  useEffect(() => {
    writeRoute(activeModule, subTab[activeModule]);
  }, [activeModule, subTab]);

  /* Listen for back/forward navigation */
  useEffect(() => {
    const onRouteChange = () => {
      const { module, sub } = parseRoute(landingFor(user.role));
      _setActiveModule(module);
      if (sub) _setSubTabState(prev => ({ ...prev, [module]: sub }));
    };
    window.addEventListener('popstate', onRouteChange);
    window.addEventListener('hashchange', onRouteChange);
    return () => {
      window.removeEventListener('popstate', onRouteChange);
      window.removeEventListener('hashchange', onRouteChange);
    };
  }, []);

  const setTenant = useCallback((newTenant: Partial<TenantContext>) => {
    if (newTenant.tenantId !== undefined) writeStorage(TENANT_KEY, newTenant.tenantId);
    if (newTenant.environment !== undefined) writeStorage(ENV_KEY, newTenant.environment);
    setTenantState(prev => ({ ...prev, ...newTenant }));
  }, []);

  /* Documents */
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);

  useEffect(() => {
    apiGet<PagedResponse<KnowledgeDocument> | KnowledgeDocument[]>('/api/data/documents')
      .then(data => {
        const items = Array.isArray(data) ? data : (data as PagedResponse<KnowledgeDocument>).items ?? [];
        setDocuments(items);
      })
      .catch(() => setDocuments([]))
      .finally(() => setDocsLoading(false));
  }, []);

  const addDocument = useCallback((doc: KnowledgeDocument) => {
    setDocuments(prev => [doc, ...prev]);
  }, []);

  const updateDocument = useCallback((data_id: string, patch: Partial<KnowledgeDocument>) => {
    setDocuments(prev => prev.map(d => {
      if (d.data_id !== data_id) return d;
      const updated = { ...d, ...patch };
      if (patch.metadata) updated.metadata = { ...d.metadata, ...patch.metadata };
      return updated;
    }));
  }, []);

  const deleteDocument = useCallback((data_id: string) => {
    setDocuments(prev => prev.filter(d => d.data_id !== data_id));
  }, []);

  /* Wizard trigger */
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  return (
    <AppStateContext.Provider
      value={{
        role,
        setRole,
        industry,
        setIndustry,
        tenant,
        setTenant,
        tenants,
        tenantsLoading,
        user,
        isExpertMode,
        setIsExpertMode,
        activeModule,
        setActiveModule,
        subTab,
        setSubTab,
        getSubTab,
        navigate,
        documents,
        docsLoading,
        addDocument,
        updateDocument,
        deleteDocument,
        pendingAction,
        setPendingAction,
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
