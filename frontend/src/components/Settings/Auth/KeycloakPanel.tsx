import React, { useState } from 'react';
import {
  Shield, ShieldCheck, ShieldAlert, Globe, Cloud, Fingerprint,
  Plus, ArrowRight, CheckCircle2, XCircle, RefreshCw, ExternalLink,
  Server, Key, AlertCircle, ChevronDown, ChevronRight, Network,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';

const HA_NODES = [
  { node: 'Keycloak Node 1', role: 'Active', status: 'HEALTHY', region: 'AZ-1' },
  { node: 'Keycloak Node 2', role: 'Active', status: 'HEALTHY', region: 'AZ-2' },
];

const HA_COMPONENTS = [
  { component: 'Load Balancer', detail: 'HAProxy — health check /health/ready', ok: true },
  { component: 'Session Sync', detail: 'Infinispan distributed cache (built-in Keycloak)', ok: true },
  { component: 'Backing Store', detail: 'PostgreSQL self-hosted (shared by both nodes)', ok: true },
  { component: 'JWKS Grace Period', detail: 'Kong cache 60s grace when Keycloak unreachable', ok: true },
];

/* ─── Realm data: r1 is the live connected realm from env vars ─────────── */
const KC_URL    = import.meta.env.VITE_KEYCLOAK_URL    ?? 'http://localhost:8080';
const KC_REALM  = import.meta.env.VITE_KEYCLOAK_REALM  ?? 'aeroflow';
const KC_CLIENT = import.meta.env.VITE_KEYCLOAK_CLIENT ?? 'aeroflow-frontend';

const REALMS = [
  {
    id: 'r1',
    tenant: 'AeroFlow Platform',
    realm_name: KC_REALM,
    keycloak_base_url: KC_URL,
    client_id: KC_CLIENT,
    jwks_url: `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/certs`,
    token_endpoint: `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/token`,
    token_ttl_seconds: 900,
    is_active: true,
    data_residency: 'Local / Self-hosted',
    isLive: true,
  },
  {
    id: 'r2',
    tenant: 'FinanceHub',
    realm_name: 'financehub-uat',
    keycloak_base_url: 'https://auth.financehub.internal',
    client_id: 'fh-platform',
    jwks_url: 'https://auth.financehub.internal/realms/financehub-uat/protocol/openid-connect/certs',
    token_endpoint: 'https://auth.financehub.internal/realms/financehub-uat/protocol/openid-connect/token',
    token_ttl_seconds: 1800,
    is_active: true,
    data_residency: 'US-East-1',
    isLive: false,
  },
  {
    id: 'r3',
    tenant: 'EuroTrust',
    realm_name: 'eurotrust-staging',
    keycloak_base_url: 'https://sso.eurotrust.eu',
    client_id: 'eurotrust-app',
    jwks_url: 'https://sso.eurotrust.eu/realms/eurotrust-staging/protocol/openid-connect/certs',
    token_endpoint: 'https://sso.eurotrust.eu/realms/eurotrust-staging/protocol/openid-connect/token',
    token_ttl_seconds: 3600,
    is_active: false,
    data_residency: 'EU-West-3',
    isLive: false,
  },
];

const FEDERATION_BRIDGES = [
  { name: 'Azure AD (Entra ID)', protocol: 'OIDC / SAML 2.0', logo: 'AZ', users: '12,402', active: true, color: '#0078D4' },
  { name: 'Okta Enterprise',     protocol: 'SAML 2.0',         logo: 'OK', users: '4,500',  active: true, color: '#007DC1' },
  { name: 'Google Workspace',    protocol: 'OIDC',             logo: 'GO', users: '820',    active: true, color: '#4285F4' },
  { name: 'WebAuthn / Passkey',  protocol: 'WebAuthn',         logo: 'WA', users: '210',    active: true, color: '#F4B400' },
  { name: 'TOTP / OTP',          protocol: 'TOTP',             logo: 'OT', users: '8,100',  active: true, color: '#0F9D58' },
];

const MFA_POLICIES = [
  { label: 'TOTP (Authenticator App)', keycloak: 'CONFIGURE_TOTP', enabled: true },
  { label: 'WebAuthn / Passkey',       keycloak: 'webauthn-register', enabled: true },
  { label: 'Concurrent Session Limit', keycloak: 'Session Limits (max 2)', enabled: true },
  { label: 'SSO Session Idle (8h)',    keycloak: 'SSO Session Idle: 8 hours', enabled: true },
  { label: 'Backchannel Logout',       keycloak: '/revoke endpoint', enabled: true },
  { label: 'IP Allowlisting (Kong)',   keycloak: 'Kong IP restriction plugin', enabled: false },
];

const ROLE_MAPPINGS = [
  { platform_role: 'PLATFORM_ADMIN', kc_role: 'platform-admin',   kc_client: 'aeroflow-backend' },
  { platform_role: 'AI_ENGINEER',    kc_role: 'ai-engineer',      kc_client: 'aeroflow-backend' },
  { platform_role: 'EXECUTIVE',      kc_role: 'executive-viewer', kc_client: 'aeroflow-backend' },
];

/* ─── Realm Detail Drawer ─────────────────────────────────────────── */
const RealmDrawer = ({
  realm,
  onClose,
}: {
  realm: (typeof REALMS)[0] | null;
  onClose: () => void;
}) => (
  <DetailDrawer
    isOpen={!!realm}
    onClose={onClose}
    title={realm?.realm_name ?? ''}
    subtitle={`Keycloak Realm Config — ${realm?.tenant}`}
    icon={Shield}
    size="md"
    footer={
      <div className="flex gap-3">
        <button onClick={onClose} className="btn-secondary">Cancel</button>
        <button className="btn-primary">Save Realm Config</button>
      </div>
    }
  >
    {realm && (
      <div className="p-6 space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Tenant</label>
            <div className="px-3 py-2 bg-[#F8F5EC] border border-[#D6C79F] rounded-xl text-sm font-medium text-[#111111]">{realm.tenant}</div>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Data Residency</label>
            <div className="px-3 py-2 bg-[#F8F5EC] border border-[#D6C79F] rounded-xl text-sm font-medium text-[#111111]">{realm.data_residency}</div>
          </div>
        </div>

        <div>
          <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Keycloak Base URL</label>
          <div className="flex items-center gap-2 px-3 py-2 bg-[#F8F5EC] border border-[#D6C79F] rounded-xl">
            <span className="text-sm font-mono text-[#111111] truncate flex-1">{realm.keycloak_base_url}</span>
            <ExternalLink className="w-3.5 h-3.5 text-[#777]" />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Client ID</label>
            <input defaultValue={realm.client_id} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Client Secret (OpenBao ref)</label>
            <input type="password" defaultValue="openbao/kv/globalcorp/kc-secret" className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
        </div>

        <div>
          <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">JWKS URL</label>
          <input defaultValue={realm.jwks_url} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
        </div>

        <div>
          <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Token Endpoint</label>
          <input defaultValue={realm.token_endpoint} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Token TTL (seconds)</label>
            <input type="number" defaultValue={realm.token_ttl_seconds} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Status</label>
            <div className={cn(
              'px-3 py-2 rounded-xl text-sm font-bold text-center',
              realm.is_active ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-red-50 text-red-700 border border-red-200',
            )}>
              {realm.is_active ? 'ACTIVE' : 'INACTIVE'}
            </div>
          </div>
        </div>

        <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-3">
          <AlertCircle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
          <p className="text-[11px] text-amber-700 leading-relaxed">
            <strong>Security note:</strong> Client secrets are never stored directly. They reference OpenBao paths (e.g. <code className="font-mono">openbao/kv/tenant/secret-name</code>). The platform fetches secrets at runtime from OpenBao.
          </p>
        </div>
      </div>
    )}
  </DetailDrawer>
);

/* ─── Main Panel ──────────────────────────────────────────────────── */
export const KeycloakPanel = () => {
  const [selectedRealm, setSelectedRealm] = useState<(typeof REALMS)[0] | null>(null);
  const [showAddRealm, setShowAddRealm] = useState(false);
  const [expandedMapping, setExpandedMapping] = useState(false);

  return (
    <>
      <div className="space-y-6">
        {/* ── Realm Configs ── */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-bold text-white">Keycloak Realm Configs</h3>
              <p className="text-xs text-slate-400 mt-0.5">Per-tenant SSO realm — 1:1 with tenant, stored in <code className="font-mono bg-white/10 px-1 rounded text-[#D9B86C]">keycloak_realm_configs</code></p>
            </div>
            <button
              onClick={() => setShowAddRealm(true)}
              className="flex items-center gap-1.5 px-3.5 py-2 bg-white/10 border border-white/20 text-white rounded-xl text-xs font-bold hover:bg-white/15 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> Add Realm
            </button>
          </div>

          <div className="space-y-2">
            {REALMS.map(realm => (
              <div
                key={realm.id}
                className="flex items-center justify-between p-4 bg-white border border-[#E8DFC8] rounded-2xl hover:border-[#B88719] hover:shadow-sm transition-all group cursor-pointer"
                onClick={() => setSelectedRealm(realm)}
              >
                <div className="flex items-center gap-4">
                  <div className={cn(
                    'w-9 h-9 rounded-xl flex items-center justify-center border shrink-0',
                    realm.is_active ? 'bg-emerald-50 border-emerald-200 text-emerald-600' : 'bg-[#F4F4F4] border-[#E0E0E0] text-[#999]',
                  )}>
                    <Shield className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-[#111111] font-mono">{realm.realm_name}</span>
                      <span className={cn(
                        'text-[9px] font-bold px-2 py-0.5 rounded-full border',
                        realm.is_active
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-[#F4F4F4] text-[#777] border-[#E0E0E0]',
                      )}>
                        {realm.is_active ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                      {(realm as any).isLive && (
                        <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-[#F4E8C3] text-[#B88719] border border-[#BFA66A]">
                          CONNECTED
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-[10px] text-[#777]">{realm.tenant}</span>
                      <span className="text-[10px] text-[#B88719] font-bold">{realm.data_residency}</span>
                      <span className="text-[10px] text-[#999] font-mono">{realm.keycloak_base_url}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                  <span className="text-[10px] text-[#777]">TTL {realm.token_ttl_seconds}s</span>
                  <button className="p-1.5 text-[#777] hover:text-[#B88719]">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                  <ArrowRight className="w-4 h-4 text-[#777]" />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── SSO Federation Bridges ── */}
        <section>
          <div className="mb-4">
            <h3 className="text-base font-bold text-white">Federated Identity Bridges</h3>
            <p className="text-xs text-slate-400 mt-0.5">Keycloak Identity Broker — SAML / OIDC federation from external providers</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {FEDERATION_BRIDGES.map(bridge => (
              <div
                key={bridge.name}
                className="flex items-center gap-3 p-4 bg-white border border-[#E8DFC8] rounded-2xl hover:border-[#B88719] transition-all group"
              >
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center text-white text-xs font-black shrink-0"
                  style={{ backgroundColor: bridge.color }}
                >
                  {bridge.logo}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold text-[#111111] truncate">{bridge.name}</div>
                  <div className="text-[10px] text-[#777]">{bridge.protocol}</div>
                  <div className="text-[10px] font-bold text-[#5A5A5A]">{bridge.users} entities</div>
                </div>
                <div className={cn(
                  'w-2 h-2 rounded-full shrink-0',
                  bridge.active ? 'bg-emerald-500' : 'bg-[#DDD]',
                )} />
              </div>
            ))}
          </div>
        </section>

        {/* ── MFA & Session Policies ── */}
        <section>
          <div className="mb-4">
            <h3 className="text-base font-bold text-white">MFA & Session Security Policies</h3>
            <p className="text-xs text-slate-400 mt-0.5">Configured in Keycloak Authentication → Required Actions & Session settings</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {MFA_POLICIES.map((policy, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-4 bg-white border border-[#E8DFC8] rounded-2xl"
              >
                <div className="flex items-center gap-3">
                  {policy.enabled
                    ? <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                    : <XCircle className="w-4 h-4 text-[#CCC] shrink-0" />
                  }
                  <div>
                    <div className="text-sm font-bold text-[#111111]">{policy.label}</div>
                    <div className="text-[10px] text-[#999] font-mono">{policy.keycloak}</div>
                  </div>
                </div>
                <span className={cn(
                  'text-[9px] font-bold px-2 py-0.5 rounded-full',
                  policy.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-[#F4F4F4] text-[#999]',
                )}>
                  {policy.enabled ? 'ON' : 'OFF'}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* ── Keycloak High Availability (Comment #12) ── */}
        <section>
          <div className="mb-4">
            <h3 className="text-base font-bold text-white">Keycloak High Availability</h3>
            <p className="text-xs text-slate-400 mt-0.5">Active/Active cluster — eliminates single point of failure for auth service</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
            {HA_NODES.map((node, i) => (
              <div key={i} className="flex items-center gap-3 p-4 bg-white border border-[#E8DFC8] rounded-2xl">
                <div className="w-9 h-9 rounded-xl bg-emerald-50 border border-emerald-200 flex items-center justify-center shrink-0">
                  <Server className="w-4 h-4 text-emerald-600" />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-bold text-[#111111]">{node.node}</div>
                  <div className="text-[10px] text-[#777]">{node.role} · {node.region}</div>
                </div>
                <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">{node.status}</span>
              </div>
            ))}
          </div>

          <div className="space-y-2">
            {HA_COMPONENTS.map((c, i) => (
              <div key={i} className="flex items-center justify-between p-3.5 bg-white border border-[#E8DFC8] rounded-xl">
                <div className="flex items-center gap-3">
                  {c.ok
                    ? <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                    : <AlertCircle className="w-4 h-4 text-amber-500 shrink-0" />}
                  <div>
                    <div className="text-sm font-bold text-[#111111]">{c.component}</div>
                    <div className="text-[10px] text-[#777]">{c.detail}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Role Mappings ── */}
        <section>
          <button
            className="w-full flex items-center justify-between p-4 bg-white border border-[#E8DFC8] rounded-2xl hover:border-[#B88719] transition-all"
            onClick={() => setExpandedMapping(v => !v)}
          >
            <div className="flex items-center gap-3">
              <ShieldCheck className="w-4 h-4 text-[#B88719]" />
              <div className="text-left">
                <div className="text-sm font-bold text-[#111111]">Keycloak Role Mappings</div>
                <div className="text-[10px] text-[#777]">Keycloak roles → Platform roles (<code className="font-mono">keycloak_role_mappings</code>)</div>
              </div>
            </div>
            {expandedMapping ? <ChevronDown className="w-4 h-4 text-[#777]" /> : <ChevronRight className="w-4 h-4 text-[#777]" />}
          </button>

          {expandedMapping && (
            <div className="mt-2 border border-[#E8DFC8] rounded-2xl overflow-hidden bg-white">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#F0E8D4] bg-[#FDFAF2]">
                    <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Platform Role</th>
                    <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Keycloak Role</th>
                    <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Keycloak Client</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#F0E8D4]">
                  {ROLE_MAPPINGS.map((m, i) => (
                    <tr key={i} className="hover:bg-[#FDFAF2] transition-colors">
                      <td className="px-4 py-2.5 font-bold text-[#111111] text-xs">{m.platform_role}</td>
                      <td className="px-4 py-2.5 font-mono text-[#B88719] text-xs">{m.kc_role}</td>
                      <td className="px-4 py-2.5 font-mono text-[#777] text-xs">{m.kc_client}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <RealmDrawer realm={selectedRealm} onClose={() => setSelectedRealm(null)} />

      {/* Add Realm Drawer */}
      <DetailDrawer
        isOpen={showAddRealm}
        onClose={() => setShowAddRealm(false)}
        title="Register Keycloak Realm"
        subtitle="New per-tenant SSO realm configuration"
        icon={Shield}
        size="md"
        footer={
          <div className="flex gap-3">
            <button onClick={() => setShowAddRealm(false)} className="btn-secondary">Cancel</button>
            <button className="btn-primary">Register Realm</button>
          </div>
        }
      >
        <div className="p-6 space-y-5">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Tenant</label>
              <select className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]">
                <option>GlobalCorp</option>
                <option>FinanceHub</option>
                <option>EuroTrust</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Realm Name</label>
              <input placeholder="my-tenant-prod" className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
            </div>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Keycloak Base URL</label>
            <input placeholder="https://auth.yourdomain.com" className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Client ID</label>
              <input placeholder="aeroflow-frontend" className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
            </div>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Token TTL (s)</label>
              <input type="number" defaultValue={900} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
            </div>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Client Secret (OpenBao Path)</label>
            <input placeholder="openbao/kv/tenant/keycloak-secret" className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]" />
            <p className="text-[10px] text-[#999] mt-1">Never store raw secrets — reference OpenBao vault path only.</p>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Data Residency</label>
            <select className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]">
              <option>Asia-SE1</option>
              <option>US-East-1</option>
              <option>EU-West-3</option>
            </select>
          </div>
        </div>
      </DetailDrawer>
    </>
  );
};
