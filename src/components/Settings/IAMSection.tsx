import React, { useState } from 'react';
import {
  Users, UserPlus, ShieldCheck, Shield, Lock,
  Eye, CheckCircle2, XCircle, ArrowRight, Bot,
  AlertCircle, RefreshCw, Loader2,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { DataTable } from '../shared/DataTable';
import { useAuth } from '../../lib/AuthProvider';
import { useKeycloakUsers, KcUser } from '../../lib/useKeycloakUsers';
import keycloak from '../../lib/keycloak';

const displayName = (u: KcUser) =>
  [u.firstName, u.lastName].filter(Boolean).join(' ') || u.username || u.email;

const formatDate = (ts: number) => {
  if (!ts) return '—';
  return new Date(ts).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
};

export const IAMSection = () => {
  const { user: authUser } = useAuth();
  const [tab, setTab] = useState<'USERS' | 'ROLES' | 'PERMISSIONS'>('USERS');

  const isAdmin = authUser?.roles.some(r => r.toUpperCase().replace(/-/g, '_') === 'PLATFORM_ADMIN') ?? false;
  const { users, loading, error, refetch } = useKeycloakUsers(isAdmin);

  /* Build display list */
  const displayUsers = isAdmin
    ? users.map(u => ({
        id: u.id,
        name: displayName(u),
        email: u.email ?? u.username,
        enabled: u.enabled,
        emailVerified: u.emailVerified,
        joinedAt: formatDate(u.createdTimestamp),
        isCurrentUser: u.id === authUser?.id,
      }))
    : authUser
      ? [{
          id: authUser.id,
          name: authUser.name || authUser.email,
          email: authUser.email,
          enabled: true,
          emailVerified: (keycloak.tokenParsed as any)?.email_verified ?? false,
          joinedAt: '—',
          isCurrentUser: true,
        }]
      : [];

  const userColumns = [
    {
      header: 'Identity',
      accessor: (u: typeof displayUsers[0]) => (
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center font-bold text-sm text-[#B88719] shrink-0">
            {u.name[0]?.toUpperCase() ?? '?'}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-white">{u.name}</span>
              {u.isCurrentUser && (
                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-[#F4E8C3] text-[#B88719] border border-[#BFA66A]">YOU</span>
              )}
            </div>
            <div className="text-[10px] text-[#777] mt-0.5">{u.email}</div>
          </div>
        </div>
      ),
    },
    {
      header: 'Status',
      accessor: (u: typeof displayUsers[0]) => (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            {u.enabled
              ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              : <XCircle className="w-3.5 h-3.5 text-red-400" />}
            <span className="text-[10px] font-bold text-[#555] uppercase tracking-wide">
              {u.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {u.emailVerified
              ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              : <AlertCircle className="w-3.5 h-3.5 text-amber-400" />}
            <span className="text-[10px] font-bold text-[#555] uppercase tracking-wide">
              Email {u.emailVerified ? 'Verified' : 'Unverified'}
            </span>
          </div>
        </div>
      ),
    },
    {
      header: 'Joined',
      accessor: (u: typeof displayUsers[0]) => (
        <span className="text-xs text-[#777] font-mono">{u.joinedAt}</span>
      ),
    },
  ];

  return (
    <div className="space-y-8">
      <OperationalHeader
        title="Identity & Access"
        subtitle={isAdmin ? 'All users in this Keycloak realm' : 'Your identity in this tenant'}
        breadcrumbs={[{ label: 'Settings' }, { label: 'IAM' }]}
        status={<StatusBadge status="STABLE" size="lg" />}
        actions={
          isAdmin && (
            <button
              onClick={refetch}
              className="flex items-center gap-2 px-4 py-2 bg-[#111111] text-white rounded-xl text-xs font-bold hover:bg-[#2a2a2a] transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Refresh
            </button>
          )
        }
      />

      <div className="flex items-center gap-2 p-1 bg-[#F4E8C3] border border-[#BFA66A] rounded-xl w-fit">
        {[
          { id: 'USERS',       label: 'Users',       icon: Users },
          { id: 'ROLES',       label: 'Roles',        icon: Shield },
          { id: 'PERMISSIONS', label: 'Permissions',  icon: Lock },
        ].map(item => (
          <button
            key={item.id}
            onClick={() => setTab(item.id as any)}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all',
              tab === item.id
                ? 'bg-white text-[#111111] shadow-sm border border-[#BFA66A]'
                : 'text-[#5A5A5A] hover:text-[#111111]',
            )}
          >
            <item.icon className={cn('w-3.5 h-3.5', tab === item.id ? 'text-[#B88719]' : 'text-[#777]')} />
            {item.label}
          </button>
        ))}
      </div>

      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.15 }}
        >
          {tab === 'USERS' && (
            <div className="space-y-4">
              {/* Loading */}
              {loading && (
                <div className="flex items-center gap-3 p-4 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
                  <Loader2 className="w-4 h-4 text-[#B88719] animate-spin" />
                  <span className="text-sm text-[#5A5A5A]">Loading users from Keycloak…</span>
                </div>
              )}

              {/* Error */}
              {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
                  <AlertCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
                  <div>
                    <p className="text-sm font-bold text-red-700">Cannot load users</p>
                    <p className="text-xs text-red-600 font-mono mt-0.5">{error}</p>
                  </div>
                </div>
              )}

              {/* Non-admin notice */}
              {!isAdmin && !loading && (
                <div className="p-3 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl flex items-center gap-2">
                  <Shield className="w-4 h-4 text-[#B88719]" />
                  <span className="text-xs text-[#5A5A5A]">Only Platform Admins can view all users in the tenant.</span>
                </div>
              )}

              {/* User count */}
              {isAdmin && !loading && !error && (
                <div className="flex items-center justify-between">
                  <span className="text-xs text-[#777]">
                    {displayUsers.length} user{displayUsers.length !== 1 ? 's' : ''} in realm <code className="font-mono bg-[#F4E8C3] px-1 rounded text-[#B88719]">{import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow'}</code>
                  </span>
                </div>
              )}

              {!loading && displayUsers.length > 0 && (
                <DataTable
                  data={displayUsers}
                  columns={userColumns as any}
                  subtitle={isAdmin ? 'Live from Keycloak Admin API' : 'Your current session'}
                />
              )}
            </div>
          )}

          {tab === 'ROLES' && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { title: 'Platform Admin', desc: 'Full access to all tenants, infrastructure, and governance.', icon: ShieldCheck, color: 'amber' },
                { title: 'AI Engineer',    desc: 'Model registry, deployment pipelines, and observability.',    icon: Bot,         color: 'emerald' },
                { title: 'Executive',      desc: 'Read-only executive dashboards and compliance reports.',      icon: Eye,         color: 'slate' },
              ].map(role => (
                <div
                  key={role.title}
                  className="p-5 bg-white border border-[#E8DFC8] rounded-2xl hover:border-[#B88719] hover:shadow-sm transition-all"
                >
                  <div className={cn(
                    'w-9 h-9 rounded-xl flex items-center justify-center mb-4 border',
                    role.color === 'amber'   ? 'bg-amber-50 border-amber-200 text-amber-600' :
                    role.color === 'emerald' ? 'bg-emerald-50 border-emerald-200 text-emerald-600' :
                    role.color === 'blue'    ? 'bg-blue-50 border-blue-200 text-blue-600' :
                    'bg-[#F4F4F4] border-[#DDD] text-[#777]',
                  )}>
                    <role.icon className="w-4 h-4" />
                  </div>
                  <h3 className="text-sm font-bold text-[#111111] mb-1">{role.title}</h3>
                  <p className="text-[11px] text-[#777] leading-relaxed">{role.desc}</p>
                </div>
              ))}
            </div>
          )}

          {tab === 'PERMISSIONS' && (
            <div className="border border-[#E8DFC8] rounded-2xl overflow-hidden bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-[#FDFAF2] border-b border-[#E8DFC8]">
                    <th className="px-4 py-3 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Capability</th>
                    {['Admin', 'AI Engineer', 'Executive'].map(r => (
                      <th key={r} className="px-4 py-3 text-center text-[10px] font-bold text-[#777] uppercase tracking-wide border-l border-[#F0E8D4]">{r}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#F0E8D4]">
                  {[
                    { cap: 'Knowledge Ingestion', perms: [true,  true,  false] },
                    { cap: 'Model Deployment',    perms: [true,  true,  false] },
                    { cap: 'Audit Log Export',    perms: [true,  false, true]  },
                    { cap: 'Tenant Management',   perms: [true,  false, false] },
                    { cap: 'Secrets Rotation',    perms: [true,  true,  false] },
                    { cap: 'Executive Reports',   perms: [true,  false, true]  },
                  ].map(({ cap, perms }) => (
                    <tr key={cap} className="hover:bg-[#FDFAF2] transition-colors">
                      <td className="px-4 py-3 text-xs font-medium text-[#333]">{cap}</td>
                      {perms.map((ok, i) => (
                        <td key={i} className="px-4 py-3 text-center border-l border-[#F0E8D4]">
                          {ok
                            ? <CheckCircle2 className="w-4 h-4 text-emerald-500 mx-auto" />
                            : <XCircle className="w-4 h-4 text-[#DDD] mx-auto" />}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
};
