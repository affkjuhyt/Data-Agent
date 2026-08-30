/**
 * Settings → Users & Permissions.
 *
 *   users        useKeycloakUsers(isAdmin)             Keycloak Admin API (real)
 *   roles        PLATFORM_ROLES = config mirroring Keycloak realm roles
 *                + current user's roles (useAuth) + GET /api/auth/me/roles
 *   permissions  usePlatformSetting('rbac_matrix')     {roles[], capabilities[], grants{role: cap[]}}
 */
import React, { useEffect, useState } from 'react';
import { Users, Shield, Lock, ShieldCheck, Bot, Eye, CheckCircle2, XCircle, AlertCircle, RefreshCw, Plus, Save, Trash2 } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { DataTable } from '../shared/DataTable';
import { Badge, Button, Card, EmptyState, ErrorState, Input, LoadingState } from '../ui';
import { useAuth } from '../../lib/AuthProvider';
import { useKeycloakUsers, KcUser } from '../../lib/useKeycloakUsers';
import { usePlatformRoles } from '../../lib/usePlatformData';
import { usePlatformSetting } from '../../lib/usePlatformSettings';
import keycloak from '../../lib/keycloak';

/** Mirrors the Keycloak realm roles the platform recognises (see Auth → Keycloak role mappings). */
const PLATFORM_ROLES = [
  { kc: 'platform-admin',   label: 'Platform Admin', desc: 'Toàn quyền tenant, hạ tầng và governance.', icon: ShieldCheck },
  { kc: 'ai-engineer',      label: 'AI Engineer',    desc: 'Model registry, pipeline triển khai, observability.', icon: Bot },
  { kc: 'executive-viewer', label: 'Executive',      desc: 'Chỉ đọc dashboard điều hành và báo cáo tuân thủ.', icon: Eye },
];

export interface RbacMatrix { roles: string[]; capabilities: string[]; grants: Record<string, string[]> }
const EMPTY_MATRIX: RbacMatrix = { roles: [], capabilities: [], grants: {} };

const displayName = (u: KcUser) => [u.firstName, u.lastName].filter(Boolean).join(' ') || u.username || u.email;
const formatDate = (ts: number) => (ts ? new Date(ts).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');

export const IAMSection = () => {
  const { user: authUser } = useAuth();
  const [tab, setTab] = useState<'USERS' | 'ROLES' | 'PERMISSIONS'>('USERS');
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
          { id: 'PERMISSIONS', label: 'Permissions', icon: Lock },
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
          {tab === 'PERMISSIONS' && <PermissionsTab canEdit={isAdmin} />}
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

/* ─── Permissions matrix (persisted) ─────────────────────── */
const PermissionsTab = ({ canEdit }: { canEdit: boolean }) => {
  const setting = usePlatformSetting<RbacMatrix>('rbac_matrix', EMPTY_MATRIX);
  const [m, setM] = useState<RbacMatrix>(EMPTY_MATRIX);
  const [dirty, setDirty] = useState(false);
  const [newRole, setNewRole] = useState('');
  const [newCap, setNewCap] = useState('');
  useEffect(() => { if (!setting.loading) { setM({ ...EMPTY_MATRIX, ...setting.value, grants: setting.value?.grants ?? {} }); setDirty(false); } }, [setting.loading, setting.value]);

  const update = (fn: (prev: RbacMatrix) => RbacMatrix) => { setM(fn); setDirty(true); };
  const has = (role: string, cap: string) => (m.grants[role] ?? []).includes(cap);
  const toggle = (role: string, cap: string) => update(prev => {
    const cur = prev.grants[role] ?? [];
    return { ...prev, grants: { ...prev.grants, [role]: cur.includes(cap) ? cur.filter(c => c !== cap) : [...cur, cap] } };
  });
  const addRole = (name: string) => { const r = name.trim(); if (!r || m.roles.includes(r)) return; update(prev => ({ ...prev, roles: [...prev.roles, r], grants: { ...prev.grants, [r]: prev.grants[r] ?? [] } })); setNewRole(''); };
  const addCap = () => { const c = newCap.trim(); if (!c || m.capabilities.includes(c)) return; update(prev => ({ ...prev, capabilities: [...prev.capabilities, c] })); setNewCap(''); };
  const removeRole = (r: string) => update(prev => { const g = { ...prev.grants }; delete g[r]; return { ...prev, roles: prev.roles.filter(x => x !== r), grants: g }; });
  const removeCap = (c: string) => update(prev => ({ ...prev, capabilities: prev.capabilities.filter(x => x !== c), grants: Object.fromEntries(Object.entries(prev.grants).map(([k, v]) => [k, v.filter(x => x !== c)])) }));
  const seedRoles = () => PLATFORM_ROLES.forEach(p => addRole(p.kc));
  const save = () => setting.save(m).then(() => setDirty(false)).catch(() => {});

  const empty = m.roles.length === 0 && m.capabilities.length === 0;

  return (
    <Card
      title="Permissions matrix"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/rbac_matrix</code>{setting.updatedAt ? ` · ${new Date(setting.updatedAt).toLocaleString('vi-VN')}` : ' · chưa lưu lần nào'}. Ma trận này là tài liệu cấu hình; việc thực thi nằm ở Keycloak/Kong.</>}
      actions={canEdit && <Button size="sm" variant="primary" icon={Save} loading={setting.saving} disabled={!dirty} onClick={save}>Lưu</Button>}
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}
      {setting.loading ? <LoadingState /> : empty && !dirty ? (
        <EmptyState
          icon={Lock}
          title="Chưa có ma trận phân quyền"
          description="Thêm role và capability, tick các ô cấp quyền rồi lưu."
          action={canEdit && <Button size="sm" icon={Plus} onClick={seedRoles}>Bắt đầu với 3 platform roles</Button>}
        />
      ) : (
        <div className="space-y-4">
          {canEdit && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <form onSubmit={e => { e.preventDefault(); addRole(newRole); }} className="flex gap-2">
                <Input value={newRole} onChange={e => setNewRole(e.target.value)} placeholder="Thêm role (vd. tenant-admin)" className="font-mono" />
                <Button type="submit" size="sm" icon={Plus}>Role</Button>
              </form>
              <form onSubmit={e => { e.preventDefault(); addCap(); }} className="flex gap-2">
                <Input value={newCap} onChange={e => setNewCap(e.target.value)} placeholder="Thêm capability (vd. secrets:rotate)" className="font-mono" />
                <Button type="submit" size="sm" icon={Plus}>Capability</Button>
              </form>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Capability</th>
                  {m.roles.map(r => (
                    <th key={r} className="text-center">
                      <span className="inline-flex items-center gap-1 font-mono">{r}{canEdit && <button type="button" onClick={() => removeRole(r)} className="text-[#5F5F5F] hover:text-[#A83A32]" aria-label={`Xoá role ${r}`}><Trash2 className="w-3 h-3" /></button>}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {m.capabilities.length === 0 ? (
                  <tr><td colSpan={m.roles.length + 1} className="text-center text-[#5F5F5F]">Chưa có capability.</td></tr>
                ) : m.capabilities.map(cap => (
                  <tr key={cap}>
                    <td>
                      <span className="inline-flex items-center gap-1.5 font-mono text-xs">{cap}{canEdit && <button type="button" onClick={() => removeCap(cap)} className="text-[#5F5F5F] hover:text-[#A83A32]" aria-label={`Xoá capability ${cap}`}><Trash2 className="w-3 h-3" /></button>}</span>
                    </td>
                    {m.roles.map(r => (
                      <td key={r} className="text-center">
                        <button type="button" disabled={!canEdit} onClick={() => toggle(r, cap)} aria-pressed={has(r, cap)} aria-label={`${r} · ${cap}`} className="disabled:cursor-not-allowed">
                          {has(r, cap) ? <CheckCircle2 className="w-4 h-4 text-[#1D6F4C] mx-auto" /> : <XCircle className="w-4 h-4 text-[#BFA66A] mx-auto" />}
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {m.roles.length === 0 && <p className="text-[11px] text-[#5F5F5F]">Chưa có role. {canEdit && <button type="button" className="underline" onClick={seedRoles}>Thêm 3 platform roles</button>}</p>}
        </div>
      )}
    </Card>
  );
};
