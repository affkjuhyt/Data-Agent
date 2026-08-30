import React from 'react';
import { Shield, Server, Key, Globe, Fingerprint, Activity, ArrowRight, CheckCircle2 } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { useAuth } from '../../../lib/AuthProvider';

const HEALTH_METRICS = [
  { label: 'Keycloak Realms',  value: '3',      sub: '2 active',          color: 'emerald' },
  { label: 'SSO Bridges',      value: '5',      sub: 'All healthy',        color: 'emerald' },
  { label: 'Active API Keys',  value: '14',     sub: '2 expiring soon',    color: 'amber' },
  { label: 'IP Rules',         value: '4',      sub: '3 active',           color: 'emerald' },
  { label: 'MFA Adoption',     value: '94%',    sub: '↑ 3% this month',    color: 'emerald' },
  { label: 'KC HA Nodes',      value: '2',      sub: 'Active/Active',      color: 'emerald' },
];

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
  { layer: 'High Availability','tech': 'Keycloak cluster Active/Active + shared PostgreSQL', role: 'Load balancer + Infinispan session sync' },
  { layer: 'Secrets',          tech: 'OpenBao',                                             role: 'No raw secrets in DB — path refs only' },
];

const STATIC_EVENTS = [
  { event: 'SECRET_ROTATION',  actor: 'System',      tenant: 'Platform',    time: '8m ago',   type: 'SYSTEM' },
  { event: 'LOGOUT',           actor: 'sarah.chen',  tenant: 'FinanceHub',  time: '22m ago',  type: 'USER' },
  { event: 'RBAC_ELEVATION',   actor: 'admin',       tenant: 'Platform',    time: '1h ago',   type: 'SYSTEM' },
  { event: 'IP_BLOCKED',       actor: '185.4.2.1',   tenant: 'EuroTrust',   time: '2h ago',   type: 'AGENT' },
];

export const AuthOverviewPanel = ({ onViewAudit }: { onViewAudit?: () => void } = {}) => {
  const { user } = useAuth();

  const recentEvents = [
    { event: 'LOGIN_SUCCESS', actor: user?.email ?? user?.name ?? 'unknown', tenant: 'AeroFlow', time: 'Now', type: 'USER' },
    ...STATIC_EVENTS,
  ];

  return (
  <div className="space-y-6">
    {/* ── Health Grid ── */}
    <section>
      <h3 className="text-base font-bold text-white mb-4">Auth Health Overview</h3>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {HEALTH_METRICS.map((m, i) => (
          <div key={i} className="p-4 bg-white border border-[#E8DFC8] rounded-2xl text-center">
            <div className={cn(
              'text-2xl font-black mb-0.5',
              m.color === 'emerald' ? 'text-emerald-600' : 'text-amber-600',
            )}>
              {m.value}
            </div>
            <div className="text-[10px] font-bold text-[#111111] uppercase tracking-wide">{m.label}</div>
            <div className="text-[9px] text-[#777] mt-0.5">{m.sub}</div>
          </div>
        ))}
      </div>
    </section>

    {/* ── Architecture summary ── */}
    <section>
      <h3 className="text-base font-bold text-white mb-4">Technology Stack</h3>
      <div className="border border-[#E8DFC8] rounded-2xl overflow-hidden bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-[#FDFAF2] border-b border-[#E8DFC8]">
              <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide w-28">Layer</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Technology</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide hidden md:table-cell">Role</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#F0E8D4]">
            {TECH_STACK.map((row, i) => (
              <tr key={i} className="hover:bg-[#FDFAF2] transition-colors">
                <td className="px-4 py-2.5 text-[10px] font-black text-[#B88719] uppercase tracking-wide">{row.layer}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-[#111111]">{row.tech}</td>
                <td className="px-4 py-2.5 text-xs text-[#777] hidden md:table-cell">{row.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>

    {/* ── Auth Audit Stream ── */}
    <section>
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-bold text-white">Recent Auth Events</h3>
        <button
          onClick={onViewAudit}
          className="text-xs font-bold text-[#B88719] hover:underline flex items-center gap-1"
        >
          View full audit stream <ArrowRight className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="space-y-2">
        {recentEvents.map((ev, i) => (
          <div key={i} className="flex items-center justify-between p-3.5 bg-white border border-[#E8DFC8] rounded-xl">
            <div className="flex items-center gap-3">
              <div className={cn(
                'w-7 h-7 rounded-lg flex items-center justify-center text-[9px] font-black shrink-0',
                ev.type === 'USER'   ? 'bg-[#E3F2FD] text-[#1565C0]' :
                ev.type === 'SYSTEM' ? 'bg-[#FFF3E0] text-[#E65100]' :
                'bg-[#FCE4EC] text-[#AD1457]',
              )}>
                {ev.type[0]}
              </div>
              <div>
                <span className="text-xs font-bold text-[#111111] font-mono">{ev.event}</span>
                <span className="ml-2 text-[10px] text-[#777]">{ev.actor}</span>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <span className="text-[10px] text-[#B88719] font-bold hidden sm:block">{ev.tenant}</span>
              <span className="text-[10px] text-[#999]">{ev.time}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 p-3 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
        <p className="text-[11px] text-[#777]">
          Auth events flow: <strong>Keycloak Event Listener SPI</strong> → Kafka topic <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit.auth.events</code> → Consumer → PostgreSQL.
          Kafka ensures at-least-once delivery — no events lost when DB is temporarily down.
          Table <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit_logs</code> — append-only, partitioned by month.
          Actor stored as string (not FK) to preserve logs after user deletion.
        </p>
      </div>
    </section>
  </div>
  );
};
