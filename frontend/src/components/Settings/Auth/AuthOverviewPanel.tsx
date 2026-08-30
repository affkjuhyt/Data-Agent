/**
 * Auth → Overview. Health tiles and the recent-events list are live:
 *   API keys   useAPIKeys()          GET /api/auth/api-keys
 *   IP rules   useIPAllowlist()      GET /api/auth/ip-allowlists
 *   audit      useAuthAuditLogs()    Keycloak events (admin only)
 *   tenants    usePlatformTenants()  GET /api/auth/me/tenants
 * TECH_STACK is architecture documentation, kept as-is.
 */
import React from 'react';
import { Key, Globe, ScrollText, Building2, ArrowRight } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { useAuth } from '../../../lib/AuthProvider';
import { useAPIKeys } from '../../../lib/useAPIKeys';
import { useIPAllowlist } from '../../../lib/useIPAllowlist';
import { useAuthAuditLogs } from '../../../lib/useAuthAuditLogs';
import { usePlatformTenants } from '../../../lib/usePlatformData';
import { Badge, Button, EmptyState, ErrorState, StatTile } from '../../ui';

const TECH_STACK = [
  { layer: 'Frontend',         tech: 'keycloak-js + @react-keycloak/web',                   role: 'Login, SSO, token refresh' },
  { layer: 'Session',          tech: 'Keycloak',                                             role: 'Idle timeout, revocation, concurrent limit' },
  { layer: 'JWT Verify',       tech: 'Kong (single point) — JWKS TTL 300s',                 role: 'Fetch/cache JWKS, force-refresh on key rotate' },
  { layer: 'Network Security', tech: 'mTLS / IP allowlist',                                  role: 'Backend only accepts requests from Kong' },
  { layer: 'Backend',          tech: 'FastAPI (read Kong headers)',                          role: 'No JWT re-verify — trusts Kong' },
  { layer: 'Admin Ops',        tech: 'python-keycloak',                                     role: 'User/role management via Admin API' },
  { layer: 'SSO/SAML',         tech: 'Keycloak Identity Broker',                            role: 'Azure AD, Okta, Google Workspace' },
  { layer: 'MFA',              tech: 'Keycloak TOTP + WebAuthn',                            role: 'Required Actions' },
  { layer: 'Audit Log',        tech: 'Keycloak → Kafka audit.auth.events → PostgreSQL',     role: 'At-least-once delivery, append-only, partition by month' },
  { layer: 'User Store',       tech: 'Keycloak internal DB',                                role: 'Identity, credentials, sessions' },
  { layer: 'High Availability', tech: 'Keycloak cluster Active/Active + shared PostgreSQL', role: 'Load balancer + Infinispan session sync' },
  { layer: 'Secrets',          tech: 'OpenBao',                                             role: 'No raw secrets in DB — path refs only' },
];

const relTime = (iso: string) => {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return iso;
  const m = Math.round(diff / 60000);
  if (m < 1) return 'vừa xong';
  if (m < 60) return `${m} phút trước`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} giờ trước`;
  return `${Math.round(h / 24)} ngày trước`;
};

export const AuthOverviewPanel = ({ onViewAudit }: { onViewAudit?: () => void } = {}) => {
  const { user } = useAuth();
  const isAdmin = user?.roles.includes('platform-admin') ?? false;
  const keys = useAPIKeys();
  const ip = useIPAllowlist();
  const audit = useAuthAuditLogs(isAdmin, 200);
  const tenants = usePlatformTenants();

  const activeKeys = keys.keys.filter(k => !k.revoked_at);
  const soon = Date.now() + 7 * 86400000;
  const expiring = activeKeys.filter(k => k.expires_at && new Date(k.expires_at).getTime() < soon);
  const activeRules = ip.rules.filter(r => r.is_active);
  const since24h = Date.now() - 86400000;
  const events24h = audit.logs.filter(l => new Date(l.time).getTime() >= since24h);
  const recent = audit.logs.slice(0, 5);

  return (
    <div className="space-y-6">
      <section>
        <h3 className="text-base font-bold text-[#111111] mb-4">Auth health</h3>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatTile label="API keys đang hoạt động" value={activeKeys.length} hint={keys.error ? 'lỗi' : expiring.length ? `${expiring.length} sắp hết hạn` : undefined} icon={Key} tone={expiring.length ? 'warn' : 'neutral'} loading={keys.loading} />
          <StatTile label="IP rules" value={ip.rules.length} hint={ip.error ? 'lỗi' : `${activeRules.length} active · ${ip.mode}`} icon={Globe} tone={ip.mode === 'allow_all' ? 'warn' : 'ok'} loading={ip.loading} />
          <StatTile label="Sự kiện auth 24h" value={isAdmin ? events24h.length : '—'} hint={!isAdmin ? 'admin only' : audit.error ? 'lỗi' : undefined} icon={ScrollText} loading={audit.loading} />
          <StatTile label="Tenants" value={tenants.data?.length ?? 0} hint={tenants.error ? 'lỗi' : undefined} icon={Building2} loading={tenants.loading} />
        </div>
      </section>

      <section>
        <h3 className="text-base font-bold text-[#111111] mb-4">Technology stack</h3>
        <div className="border border-[#BFA66A] rounded-2xl overflow-hidden bg-white">
          <table className="ui-table">
            <thead>
              <tr><th className="w-28">Layer</th><th>Technology</th><th className="hidden md:table-cell">Role</th></tr>
            </thead>
            <tbody>
              {TECH_STACK.map(row => (
                <tr key={row.layer}>
                  <td className="text-[10px] font-bold text-[#8A5A00] uppercase tracking-wide">{row.layer}</td>
                  <td className="font-mono text-xs">{row.tech}</td>
                  <td className="text-xs text-[#3F3F3F] hidden md:table-cell">{row.role}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-bold text-[#111111]">Sự kiện auth gần đây</h3>
          {onViewAudit && <Button size="sm" variant="ghost" icon={ArrowRight} onClick={onViewAudit}>Xem toàn bộ audit</Button>}
        </div>
        {!isAdmin ? (
          <EmptyState icon={ScrollText} title="Chỉ Platform Admin xem được audit stream" />
        ) : audit.error ? (
          <ErrorState message={audit.error} onRetry={audit.refetch} />
        ) : recent.length === 0 && !audit.loading ? (
          <EmptyState icon={ScrollText} title="Chưa có sự kiện" description="Keycloak chưa ghi nhận sự kiện nào cho tenant này." />
        ) : (
          <div className="space-y-2">
            {recent.map(ev => (
              <div key={ev.id} className="flex items-center justify-between gap-3 p-3.5 bg-white border border-[#BFA66A] rounded-xl">
                <div className="flex items-center gap-3 min-w-0">
                  <div className={cn('w-7 h-7 rounded-lg flex items-center justify-center text-[9px] font-black shrink-0', ev.id.startsWith('A-') ? 'bg-[#FFF3E0] text-[#E65100]' : 'bg-[#E3F2FD] text-[#1565C0]')}>
                    {ev.id.startsWith('A-') ? 'A' : 'U'}
                  </div>
                  <div className="min-w-0">
                    <span className="text-xs font-bold text-[#111111] font-mono">{ev.event}</span>
                    <span className="ml-2 text-[10px] text-[#5F5F5F] truncate">{ev.actor}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <Badge tone={ev.outcome === 'SUCCESS' ? 'healthy' : 'failed'}>{ev.outcome}</Badge>
                  <span className="text-[10px] text-[#5F5F5F]">{relTime(ev.time)}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="mt-3 p-3 bg-[#FFF9E8] border border-[#BFA66A] rounded-xl">
          <p className="text-[11px] text-[#3F3F3F]">
            Auth events flow: <strong>Keycloak Event Listener SPI</strong> → Kafka topic <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit.auth.events</code> → Consumer → PostgreSQL.
            Kafka ensures at-least-once delivery. Table <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit_logs</code> — append-only, partitioned by month.
          </p>
        </div>
      </section>
    </div>
  );
};
