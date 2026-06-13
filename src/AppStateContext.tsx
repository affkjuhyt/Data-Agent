import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { Role, Industry, TenantContext, User, Environment, KnowledgeDocument, PagedResponse } from './types';
import { mockGet } from './lib/mockApi';
import { useAuth } from './lib/AuthProvider';

interface AppStateContextType {
  role: Role;
  setRole: (role: Role) => void;
  industry: Industry;
  setIndustry: (industry: Industry) => void;
  tenant: TenantContext;
  setTenant: (tenant: Partial<TenantContext>) => void;
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

/* ─── URL hash helpers ─────────────────────────────────────────────── */
const parseHash = (): { module: string; sub?: string } => {
  const raw = (typeof window !== 'undefined' ? window.location.hash : '').replace(/^#\/?/, '');
  if (!raw) return { module: 'dashboard' };
  const [module, sub] = raw.split('/');
  return { module: module || 'dashboard', sub: sub || undefined };
};

const writeHash = (module: string, sub?: string) => {
  if (typeof window === 'undefined') return;
  const next = sub ? `#${module}/${sub}` : `#${module}`;
  if (window.location.hash !== next) {
    // Preserve query params so Keycloak can read ?code=&state= on auth redirect
    window.history.replaceState(null, '', window.location.search + next);
  }
};

export const AppStateProvider = ({ children }: { children: ReactNode }) => {
  const [role, setRole] = useState<Role>('AI_ENGINEER');
  const [industry, setIndustry] = useState<Industry>('GENERAL');
  const [isExpertMode, setIsExpertMode] = useState(false);
  const [tenant, setTenantState] = useState<TenantContext>({
    organization: 'Global Corp',
    workspace: 'Engineering Alpha',
    project: 'Nexus-7',
    environment: 'PROD',
  });

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

  /* Routing state — initialized from URL hash */
  const initial = parseHash();
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
    _setActiveModule(moduleId);
    if (subId) {
      _setSubTabState(prev => ({ ...prev, [moduleId]: subId }));
    }
  }, []);

  /* Keep URL hash in sync with state */
  useEffect(() => {
    writeHash(activeModule, subTab[activeModule]);
  }, [activeModule, subTab]);

  /* Listen for back/forward navigation */
  useEffect(() => {
    const onHashChange = () => {
      const { module, sub } = parseHash();
      _setActiveModule(module);
      if (sub) _setSubTabState(prev => ({ ...prev, [module]: sub }));
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const setTenant = (newTenant: Partial<TenantContext>) => {
    setTenantState(prev => ({ ...prev, ...newTenant }));
  };

  /* Documents */
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [docsLoading, setDocsLoading] = useState(true);

  useEffect(() => {
    mockGet<PagedResponse<KnowledgeDocument> | KnowledgeDocument[]>('/api/data/documents')
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
