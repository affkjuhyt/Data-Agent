import React, { useState } from 'react';
import {
  Server, Shield, CheckCircle2, Copy, RefreshCw, ArrowRight,
  Zap, Lock, Globe, AlertCircle, Eye, EyeOff, Activity, Network,
} from 'lucide-react';
import { cn } from '../../../lib/utils';

const KC_URL   = import.meta.env.VITE_KEYCLOAK_URL   ?? 'http://localhost:8080';
const KC_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow';

/* Kong OIDC plugin config — discovery URL uses real env vars */
const KONG_PLUGIN_CONFIG = `plugins:
  - name: oidc
    config:
      discovery: ${KC_URL}/realms/${KC_REALM}/.well-known/openid-configuration
      # JWKS cache TTL: 300s (Comment #0)
      # Force-refresh via Kong Admin API when Keycloak rotates key
      jwks_uri_refresh_interval: 300
      # After JWT verify, injects headers for backend:
      header_names:
        - X-User-Id
        - X-User-Roles
        - X-User-Email`;

const BACKEND_TRUST_CONFIG = `# FastAPI — read Kong-injected headers, no JWT re-verify
from fastapi import Header

def get_current_user(
    user_id: str    = Header(..., alias="X-User-Id"),
    user_roles: str = Header(..., alias="X-User-Roles"),
):
    return {"user_id": user_id, "roles": user_roles.split(",")}`;

const TENANTS_STATUS = [
  { tenant: 'AeroFlow Platform', realm: KC_REALM, jwks_cached: true, last_verified: 'Live', requests_1h: '—', isLive: true },
  { tenant: 'FinanceHub',  realm: 'financehub-uat',     jwks_cached: true,  last_verified: '4m ago',   requests_1h: '12,840', isLive: false },
  { tenant: 'EuroTrust',   realm: 'eurotrust-staging',  jwks_cached: false, last_verified: 'N/A',      requests_1h: '0', isLive: false },
];

const INJECTED_HEADERS = [
  { header: 'X-User-Id',    source: 'sub claim',   example: 'kc-user-uuid-abc123' },
  { header: 'X-User-Roles', source: 'realm_roles', example: 'PLATFORM_ADMIN,AI_ENGINEER' },
  { header: 'X-User-Email', source: 'email claim', example: 'user@tenant.com' },
];

const FLOW_STEPS = [
  { step: 1, label: 'User → Keycloak',     desc: 'Login / SSO / MFA flow',                            color: 'bg-[#F3E2A7] text-[#111111]' },
  { step: 2, label: 'Keycloak → Frontend', desc: 'JWT (RS256) returned to browser',                    color: 'bg-[#F3E2A7] text-[#111111]' },
  { step: 3, label: 'Frontend → Kong',     desc: 'Bearer token in Authorization header',               color: 'bg-[#E8F5E9] text-[#1B5E20]' },
  { step: 4, label: 'Kong verifies JWT',   desc: 'JWKS cached TTL 300s — force-refresh on key rotate', color: 'bg-[#E3F2FD] text-[#0D47A1]' },
  { step: 5, label: 'Kong → Backend',      desc: 'Injects X-User-Id / X-User-Roles / X-User-Email',   color: 'bg-[#FFF3E0] text-[#E65100]' },
  { step: 6, label: 'Backend reads header', desc: 'No JWT re-verify — IP allowlist / mTLS enforced',   color: 'bg-[#F3E5F5] text-[#4A148C]' },
];

const NETWORK_SECURITY = [
  { method: 'IP Allowlist', desc: 'Backend only accepts traffic from Kong CIDR range', status: 'ACTIVE' },
  { method: 'mTLS (service-to-service)', desc: 'Kong presents client cert; backend validates CA', status: 'ACTIVE' },
  { method: 'Kong IP restriction plugin', desc: 'Blocks direct access bypassing Kong gateway', status: 'ACTIVE' },
];

export const KongGatewayPanel = () => {
  const [copiedPlugin, setCopiedPlugin] = useState(false);
  const [copiedBackend, setCopiedBackend] = useState(false);
  const [showJwksUrl, setShowJwksUrl] = useState<Record<string, boolean>>({});

  const copy = (text: string, setter: (v: boolean) => void) => {
    navigator.clipboard.writeText(text).then(() => {
      setter(true);
      setTimeout(() => setter(false), 1800);
    });
  };

  return (
    <div className="space-y-6">
      {/* ── Auth Flow Architecture ── */}
      <section>
        <div className="mb-4">
          <h3 className="text-base font-bold text-white">Auth Flow Architecture</h3>
          <p className="text-xs text-slate-400 mt-0.5">Kong is the single JWT verification point — backends trust injected headers</p>
        </div>

        <div className="flex flex-col gap-2">
          {FLOW_STEPS.map((s, i) => (
            <div key={i} className="flex items-center gap-3">
              <div className={cn('w-7 h-7 rounded-full flex items-center justify-center text-xs font-black shrink-0', s.color)}>
                {s.step}
              </div>
              <div className="flex-1 flex items-center justify-between p-3 bg-white border border-[#E8DFC8] rounded-xl">
                <span className="text-sm font-bold text-[#111111]">{s.label}</span>
                <span className="text-[11px] text-[#777]">{s.desc}</span>
              </div>
              {i < FLOW_STEPS.length - 1 && (
                <div className="w-7 flex justify-center">
                  <div className="w-px h-4 bg-[#D6C79F] -mt-2" />
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* ── Kong OIDC Plugin Config ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-base font-bold text-white">Kong OIDC Plugin</h3>
            <p className="text-xs text-slate-400">Deployed per-route or globally via Kong Admin API</p>
          </div>
          <button
            onClick={() => copy(KONG_PLUGIN_CONFIG, setCopiedPlugin)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#F4E8C3] border border-[#BFA66A] rounded-lg text-xs font-bold text-[#3F3F3F] hover:bg-[#EDD9A3] transition-colors"
          >
            <Copy className="w-3.5 h-3.5" />
            {copiedPlugin ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <pre className="p-4 bg-[#111111] text-[#D9B86C] rounded-2xl text-[11px] font-mono leading-relaxed overflow-x-auto border border-[#2a2a2a]">
          {KONG_PLUGIN_CONFIG}
        </pre>
      </section>

      {/* ── Injected Headers ── */}
      <section>
        <div className="mb-3">
          <h3 className="text-base font-bold text-white">Injected Headers (Backend contract)</h3>
          <p className="text-xs text-slate-400">Kong sets these after JWT verification — backends read directly, no re-verify</p>
        </div>

        <div className="border border-[#E8DFC8] rounded-2xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#FDFAF2] border-b border-[#E8DFC8]">
                <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Header</th>
                <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">JWT Source</th>
                <th className="px-4 py-2.5 text-left text-[10px] font-bold text-[#777] uppercase tracking-wide">Example Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#F0E8D4]">
              {INJECTED_HEADERS.map((h, i) => (
                <tr key={i} className="hover:bg-[#FDFAF2]">
                  <td className="px-4 py-2.5 font-mono text-[#B88719] font-bold text-xs">{h.header}</td>
                  <td className="px-4 py-2.5 text-[#777] text-xs">{h.source}</td>
                  <td className="px-4 py-2.5 font-mono text-[#3A3A3A] text-xs">{h.example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Backend FastAPI snippet ── */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-base font-bold text-white">Backend — FastAPI Trust Pattern</h3>
            <p className="text-xs text-slate-400">Backend does NOT verify JWT — reads Kong headers only</p>
          </div>
          <button
            onClick={() => copy(BACKEND_TRUST_CONFIG, setCopiedBackend)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#F4E8C3] border border-[#BFA66A] rounded-lg text-xs font-bold text-[#3F3F3F] hover:bg-[#EDD9A3] transition-colors"
          >
            <Copy className="w-3.5 h-3.5" />
            {copiedBackend ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <pre className="p-4 bg-[#111111] text-[#98C379] rounded-2xl text-[11px] font-mono leading-relaxed overflow-x-auto border border-[#2a2a2a]">
          {BACKEND_TRUST_CONFIG}
        </pre>
      </section>

      {/* ── Per-tenant JWKS Status ── */}
      <section>
        <div className="mb-4">
          <h3 className="text-base font-bold text-white">JWKS Cache Status</h3>
          <p className="text-xs text-slate-400">Kong fetches Keycloak JWKS at first request per realm and caches automatically</p>
        </div>

        <div className="space-y-2">
          {TENANTS_STATUS.map(t => (
            <div key={t.tenant} className="flex items-center justify-between p-4 bg-white border border-[#E8DFC8] rounded-2xl">
              <div className="flex items-center gap-3">
                <div className={cn(
                  'w-2 h-2 rounded-full shrink-0',
                  t.jwks_cached ? 'bg-emerald-500' : 'bg-[#DDD]',
                )} />
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-[#111111]">{t.tenant}</span>
                    {(t as any).isLive && (
                      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-[#F4E8C3] text-[#B88719] border border-[#BFA66A]">CONNECTED</span>
                    )}
                  </div>
                  <span className="text-[10px] font-mono text-[#777]">{t.realm}</span>
                </div>
              </div>
              <div className="flex items-center gap-6 text-xs">
                <div className="text-right">
                  <div className="text-[10px] text-[#777]">Last verified</div>
                  <div className="font-bold text-[#111111]">{t.last_verified}</div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] text-[#777]">Requests/1h</div>
                  <div className="font-bold text-[#111111]">{t.requests_1h}</div>
                </div>
                <span className={cn(
                  'text-[9px] font-bold px-2 py-0.5 rounded-full',
                  t.jwks_cached ? 'bg-emerald-50 text-emerald-700' : 'bg-[#F4F4F4] text-[#999]',
                )}>
                  {t.jwks_cached ? 'CACHED' : 'INACTIVE'}
                </span>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-2.5">
          <AlertCircle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
          <p className="text-[11px] text-amber-700">
            System keys at 84% TTL. Automated rotation scheduled for next Sunday.
            Kong JWKS cache (<code className="font-mono">jwks_uri_refresh_interval: 300s</code>) will force-refresh via Kong Admin API event hook after rotation.
          </p>
        </div>
      </section>

      {/* ── Network Segmentation (Comment #1) ── */}
      <section>
        <div className="mb-3">
          <h3 className="text-base font-bold text-white">Network Segmentation</h3>
          <p className="text-xs text-slate-400">Backend only accepts requests from Kong — prevents header spoofing of X-User-Id / X-User-Roles</p>
        </div>

        <div className="space-y-2">
          {NETWORK_SECURITY.map((item, i) => (
            <div key={i} className="flex items-center justify-between p-4 bg-white border border-[#E8DFC8] rounded-2xl">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#E8F5E9] flex items-center justify-center shrink-0">
                  <Lock className="w-4 h-4 text-emerald-600" />
                </div>
                <div>
                  <div className="text-sm font-bold text-[#111111]">{item.method}</div>
                  <div className="text-[10px] text-[#777]">{item.desc}</div>
                </div>
              </div>
              <span className="text-[9px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700">
                {item.status}
              </span>
            </div>
          ))}
        </div>

        <div className="mt-3 p-3 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
          <p className="text-[11px] text-[#777]">
            Network segmentation ensures malicious clients cannot inject fake <code className="font-mono bg-[#F4E8C3] px-1 rounded">X-User-Id</code> / <code className="font-mono bg-[#F4E8C3] px-1 rounded">X-User-Roles</code> headers by bypassing Kong.
          </p>
        </div>
      </section>
    </div>
  );
};
