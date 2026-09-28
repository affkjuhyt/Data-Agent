import React, { useState } from 'react';
import { Shield, Key, Globe, LayoutGrid, ScrollText } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { useAuth } from '../../lib/AuthProvider';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { AuthOverviewPanel } from './Auth/AuthOverviewPanel';
import { KeycloakPanel } from './Auth/KeycloakPanel';
import { IPAllowlistPanel } from './Auth/IPAllowlistPanel';
import { APIKeysPanel } from './Auth/APIKeysPanel';
import { AuthAuditPanel } from './Auth/AuthAuditPanel';
import { MicrosoftSSOPanel } from './Auth/MicrosoftSSOPanel';

type AuthTab = 'OVERVIEW' | 'KEYCLOAK' | 'IP' | 'APIKEYS' | 'AUDIT' | 'MSAPP';

const ALL_TABS: { id: AuthTab; label: string; icon: React.ElementType; desc: string; adminOnly?: boolean; approvalAdmin?: boolean }[] = [
  { id: 'OVERVIEW', label: 'Overview',      icon: LayoutGrid,  desc: 'Health, stack & audit stream' },
  { id: 'KEYCLOAK', label: 'Keycloak',      icon: Shield,      desc: 'Realms, SSO bridges, role mappings',               adminOnly: true },
  { id: 'MSAPP',    label: 'MS Approvals',  icon: Shield,      desc: 'Microsoft SSO — first-login admin approval queue',  approvalAdmin: true },
  { id: 'IP',       label: 'IP Allowlist',  icon: Globe,       desc: 'CIDR whitelist via Kong IP plugin',                adminOnly: true },
  { id: 'APIKEYS',  label: 'API Keys',      icon: Key,         desc: 'Programmatic access, rotate & revoke' },
  { id: 'AUDIT',    label: 'Audit Log',     icon: ScrollText,  desc: 'Full Keycloak auth event stream — admin only',     adminOnly: true },
];

export const AuthSection = ({ initialTab }: { initialTab?: AuthTab } = {}) => {
  const { user } = useAuth();
  const isPlatformAdmin = user?.roles.includes('platform-admin') ?? false;
  const isTenantAdmin   = user?.roles.includes('tenant-admin') ?? false;

  const TABS = ALL_TABS.filter(t => {
    if (t.adminOnly)     return isPlatformAdmin;
    if (t.approvalAdmin) return isPlatformAdmin || isTenantAdmin;
    return true;
  });

  const [tab, setTab] = useState<AuthTab>(initialTab ?? 'OVERVIEW');

  return (
    <div className="space-y-6 lg:space-y-8 pb-20">
      <OperationalHeader
        title="Auth & SSO"
        subtitle="Keycloak SSO · API Keys · IP Allowlist · Audit"
        breadcrumbs={[{ label: 'Settings' }, { label: 'Auth & SSO' }]}
        status={<StatusBadge status="ACTIVE" size="lg" />}
      />

      {/* Tab bar */}
      <div className="flex flex-wrap gap-2">
        {TABS.map(t => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'flex items-center gap-2 px-4 py-2.5 rounded-xl border text-xs font-bold transition-all',
                active
                  ? 'bg-[#111111] text-white border-[#111111] shadow-sm'
                  : 'bg-white text-[#3F3F3F] border-[#D6C79F] hover:border-[#B88719] hover:bg-[#FFF9E8]',
              )}
            >
              <Icon className={cn('w-3.5 h-3.5', active ? 'text-[#D9B86C]' : 'text-[#777]')} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab description */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
        {(() => { const Icon = TABS.find(t => t.id === tab)!.icon; return <Icon className="w-3.5 h-3.5 text-[#B88719]" />; })()}
        <span className="text-xs text-[#5A5A5A]">{TABS.find(t => t.id === tab)!.desc}</span>
      </div>

      {/* Panel */}
      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.15, ease: [0.4, 0, 0.2, 1] }}
        >
          {tab === 'OVERVIEW' && <AuthOverviewPanel onViewAudit={() => setTab('AUDIT')} />}
          {tab === 'KEYCLOAK' && <KeycloakPanel />}
          {tab === 'MSAPP'    && <MicrosoftSSOPanel />}
          {tab === 'IP'       && <IPAllowlistPanel />}
          {tab === 'APIKEYS'  && <APIKeysPanel />}
          {tab === 'AUDIT'    && <AuthAuditPanel />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
};
