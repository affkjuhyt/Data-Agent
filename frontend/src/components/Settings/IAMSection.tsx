/**
 * Settings → Users & Permissions.
 *
 *   users        useKeycloakUsers(isAdmin)             Keycloak Admin API (real)
 *   roles        PLATFORM_ROLES = config mirroring Keycloak realm roles
 *                + current user's roles (useAuth) + GET /api/auth/me/roles
 *
 * No editable permissions matrix: enforcement is Keycloak roles + Kong, and
 * nothing server-side read the matrix it saved.
 */
import { useState } from 'react';
import { Users, Shield, ShieldCheck, Bot, Eye, CheckCircle2, XCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { DataTable } from '../shared/DataTable';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState } from '../ui';
import { useAuth } from '../../lib/AuthProvider';
import { useKeycloakUsers, KcUser } from '../../lib/useKeycloakUsers';
import { usePlatformRoles } from '../../lib/usePlatformData';
import keycloak from '../../lib/keycloak';

/** Mirrors the Keycloak realm roles the platform recognises (see Auth → Keycloak role mappings). */
const PLATFORM_ROLES = [
  { kc: 'platform-admin',   label: 'Platform Admin', desc: 'Toàn quyền tenant, hạ tầng và governance.', icon: ShieldCheck },
  { kc: 'ai-engineer',      label: 'AI Engineer',    desc: 'Model registry, pipeline triển khai, observability.', icon: Bot },
  { kc: 'executive-viewer', label: 'Executive',      desc: 'Chỉ đọc dashboard điều hành và báo cáo tuân thủ.', icon: Eye },
];

const displayName = (u: KcUser) => [u.firstName, u.lastName].filter(Boolean).join(' ') || u.username || u.email;
const formatDate = (ts: number) => (ts ? new Date(ts).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');

export const IAMSection = () => {
  const { user: authUser } = useAuth();
  const [tab, setTab] = useState<'USERS' | 'ROLES'>('USERS');
  const isAdmin = authUser?.roles.some(r => r.toUpperCase().replace(/-/g, '_') === 'PLATFORM_ADMIN') ?? false;
  const { users, loading, error, refetch } = useKeycloakUsers(isAdmin);

  const displayUsers = isAdmin
    ? users.map(u => ({ id: u.id, name: displayName(u), email: u.email ?? u.username, enabled: u.enabled, emailVerified: u.emailVerified, joinedAt: formatDate(u.createdTimestamp), isCurrentUser: u.id === authUser?.id }))
    : authUser
      ? [{ id: authUser.id, name: authUser.name || authUser.email, email: authUser.email, enabled: true, emailVerified: (keycloak.tokenParsed as any)?.email_verified ?? false, joinedAt: '—', isCurrentUser: true }]
      : [];

  const userColumns = [
    {
      header: 'Identity',
      accessor: (u: typeof displayUsers[0]) => (
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center font-bold text-sm text-[#8A5A00] shrink-0">{u.name[0]?.toUpperCase() ?? '?'}</div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-[#111111]">{u.name}</span>
              {u.isCurrentUser && <Badge tone="dark">you</Badge>}
            </div>
            <div className="text-[10px] text-[#5F5F5F] mt-0.5">{u.email}</div>
          </div>
        </div>
      ),
    },
    {
      header: 'Status',
      accessor: (u: typeof displayUsers[0]) => (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            {u.enabled ? <CheckCircle2 className="w-3.5 h-3.5 text-[#1D6F4C]" /> : <XCircle className="w-3.5 h-3.5 text-[#A83A32]" />}
            <span className="text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide">{u.enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
          <div className="flex items-center gap-1.5">
            {u.emailVerified ? <CheckCircle2 className="w-3.5 h-3.5 text-[#1D6F4C]" /> : <AlertCircle className="w-3.5 h-3.5 text-[#8A6212]" />}
            <span className="text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide">Email {u.emailVerified ? 'Verified' : 'Unverified'}</span>
          </div>
        </div>
      ),
    },
    { header: 'Joined', accessor: (u: typeof displayUsers[0]) => <span className="text-xs text-[#5F5F5F] font-mono">{u.joinedAt}</span> },
  ];

  return (
    <div className="space-y-8">
      <OperationalHeader
        title="Identity & Access"
        subtitle={isAdmin ? 'Toàn bộ user trong realm Keycloak' : 'Danh tính của bạn trong tenant này'}
        breadcrumbs={[{ label: 'Settings' }, { label: 'IAM' }]}
        status={<StatusBadge status="STABLE" size="lg" />}
        actions={isAdmin && <Button icon={RefreshCw} loading={loading} onClick={refetch}>Tải lại</Button>}
      />

      <div className="flex items-center gap-2 p-1 bg-[#F4E8C3] border border-[#BFA66A] rounded-xl w-fit">
        {[
          { id: 'USERS', label: 'Users', icon: Users },
          { id: 'ROLES', label: 'Roles', icon: Shield },
        ].map(item => (
          <button key={item.id} type="button" onClick={() => setTab(item.id as any)} className={cn('flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all', tab === item.id ? 'bg-white text-[#111111] shadow-sm border border-[#BFA66A]' : 'text-[#3F3F3F] hover:text-[#111111]')}>
            <item.icon className={cn('w-3.5 h-3.5', tab === item.id ? 'text-[#8A5A00]' : 'text-[#5F5F5F]')} />
            {item.label}
          </button>
        ))}
      </div>

      <AnimatePresence mode="wait">
        <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.15 }}>
          {tab === 'USERS' && (
            <div className="space-y-4">
              {loading && <LoadingState label="Đang tải user từ Keycloak…" />}
              {error && <ErrorState message={error} onRetry={refetch} />}
              {!isAdmin && !loading && (
                <div className="p-3 bg-[#FFF9E8] border border-[#BFA66A] rounded-xl flex items-center gap-2">
                  <Shield className="w-4 h-4 text-[#8A5A00]" />
                  <span className="text-xs text-[#3F3F3F]">Chỉ Platform Admin mới xem được toàn bộ user trong tenant.</span>
                </div>
              )}
              {isAdmin && !loading && !error && (
                <span className="text-xs text-[#5F5F5F]">
                  {displayUsers.length} user trong realm <code className="font-mono bg-[#F4E8C3] px-1 rounded text-[#8A5A00]">{import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow'}</code>
                </span>
              )}
              {!loading && displayUsers.length > 0 && (
                <DataTable data={displayUsers} columns={userColumns as any} subtitle={isAdmin ? 'Live từ Keycloak Admin API' : 'Phiên hiện tại của bạn'} />
              )}
              {!loading && !error && displayUsers.length === 0 && <EmptyState icon={Users} title="Không có user" />}
            </div>
          )}

          {tab === 'ROLES' && <RolesTab userRoles={authUser?.roles ?? []} />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
};

/* ─── Roles ──────────────────────────────────────────────── */
const RolesTab = ({ userRoles }: { userRoles: string[] }) => {
  const available = usePlatformRoles();
  return (
    <div className="space-y-6">
      <Card title="Platform roles (config)" subtitle="Ba role này mirror realm roles trong Keycloak; gán role được thực hiện trong Keycloak Admin, không ở đây.">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {PLATFORM_ROLES.map(role => {
            const mine = userRoles.includes(role.kc);
            return (
              <div key={role.kc} className={cn('p-4 rounded-xl border bg-white', mine ? 'border-[#8A5A00]' : 'border-[#BFA66A]')}>
                <div className="flex items-center justify-between mb-3">
                  <span className="w-9 h-9 rounded-xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center text-[#8A5A00]"><role.icon className="w-4 h-4" /></span>
                  {mine && <Badge tone="healthy">bạn có role này</Badge>}
                </div>
                <h3 className="text-sm font-bold text-[#111111]">{role.label}</h3>
                <code className="text-[11px] font-mono text-[#8A5A00]">{role.kc}</code>
                <p className="text-[11px] text-[#5F5F5F] mt-2 leading-relaxed">{role.desc}</p>
              </div>
            );
          })}
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card title="Roles của bạn" subtitle="realm_access.roles trong JWT hiện tại">
          {userRoles.length === 0 ? <EmptyState icon={Shield} title="Token không có role" /> : (
            <div className="flex flex-wrap gap-2">{userRoles.map(r => <Badge key={r} tone={PLATFORM_ROLES.some(p => p.kc === r) ? 'healthy' : 'draft'}>{r}</Badge>)}</div>
          )}
        </Card>
        <Card title="Roles có thể yêu cầu" subtitle="GET /api/auth/me/roles">
          {available.error ? <ErrorState message={available.error} onRetry={available.reload} /> : available.loading ? <LoadingState /> : (available.data?.roles.length ?? 0) === 0 ? (
            <EmptyState icon={Shield} title="Không có role nào được expose" />
          ) : (
            <div className="flex flex-wrap gap-2">{available.data!.roles.map(r => <Badge key={r} tone="draft">{r}</Badge>)}</div>
          )}
        </Card>
      </div>
    </div>
  );
};
