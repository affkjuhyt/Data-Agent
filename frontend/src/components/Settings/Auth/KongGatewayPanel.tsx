/**
 * Auth → Kong Gateway. Documentation of the gateway contract (flow, plugin
 * config, injected headers, trust pattern). Kong Admin API is not exposed to
 * the frontend, so there are no live per-tenant request stats here.
 */
import React, { useState } from 'react';
import { Copy, Lock, Server } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { Card, EmptyState } from '../../ui';

const KC_URL   = import.meta.env.VITE_KEYCLOAK_URL   ?? 'http://localhost:8080';
const KC_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow';
const JWKS_URL = `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/certs`;

const KONG_PLUGIN_CONFIG = `plugins:
  - name: oidc
    config:
      discovery: ${KC_URL}/realms/${KC_REALM}/.well-known/openid-configuration
      # JWKS cache TTL: 300s — force-refresh via Kong Admin API when Keycloak rotates keys
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

const INJECTED_HEADERS = [
  { header: 'X-User-Id',    source: 'sub claim',   example: 'kc-user-uuid' },
  { header: 'X-User-Roles', source: 'realm_roles', example: 'platform-admin,ai-engineer' },
  { header: 'X-User-Email', source: 'email claim', example: 'user@tenant.example' },
];

const FLOW_STEPS = [
  { step: 1, label: 'User → Keycloak',      desc: 'Login / SSO / MFA flow',                            color: 'bg-[#F3E2A7] text-[#111111]' },
  { step: 2, label: 'Keycloak → Frontend',  desc: 'JWT (RS256) returned to browser',                   color: 'bg-[#F3E2A7] text-[#111111]' },
  { step: 3, label: 'Frontend → Kong',      desc: 'Bearer token in Authorization header',              color: 'bg-[#E8F5E9] text-[#1B5E20]' },
  { step: 4, label: 'Kong verifies JWT',    desc: 'JWKS cached TTL 300s — force-refresh on key rotate', color: 'bg-[#E3F2FD] text-[#0D47A1]' },
  { step: 5, label: 'Kong → Backend',       desc: 'Injects X-User-Id / X-User-Roles / X-User-Email',   color: 'bg-[#FFF3E0] text-[#E65100]' },
  { step: 6, label: 'Backend reads header', desc: 'No JWT re-verify — IP allowlist / mTLS enforced',   color: 'bg-[#F3E5F5] text-[#4A148C]' },
];

const NETWORK_SECURITY = [
  { method: 'IP allowlist',                desc: 'Backend chỉ nhận traffic từ dải CIDR của Kong' },
  { method: 'mTLS (service-to-service)',   desc: 'Kong trình client cert; backend xác thực CA' },
  { method: 'Kong IP restriction plugin',  desc: 'Chặn truy cập trực tiếp bỏ qua gateway' },
];

const CodeBlock = ({ title, subtitle, code, tone }: { title: string; subtitle: string; code: string; tone: string }) => {
  const [copied, setCopied] = useState(false);
  const copy = () => navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); });
  return (
    <Card title={title} subtitle={subtitle} actions={
      <button type="button" onClick={copy} className="btn-secondary btn-sm inline-flex items-center gap-1.5"><Copy className="w-3.5 h-3.5" />{copied ? 'Đã copy' : 'Copy'}</button>
    }>
      <pre className={cn('p-4 bg-[#111111] rounded-xl text-[11px] font-mono leading-relaxed overflow-x-auto', tone)}>{code}</pre>
    </Card>
  );
};

export const KongGatewayPanel = () => (
  <div className="space-y-6">
    <Card title="Auth flow" subtitle="Kong là điểm xác thực JWT duy nhất — backend tin header được inject">
      <ol className="space-y-2">
        {FLOW_STEPS.map(s => (
          <li key={s.step} className="flex items-center gap-3">
            <span className={cn('w-7 h-7 rounded-full flex items-center justify-center text-xs font-black shrink-0', s.color)}>{s.step}</span>
            <div className="flex-1 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 p-3 bg-white border border-[#BFA66A] rounded-xl">
              <span className="text-sm font-bold text-[#111111]">{s.label}</span>
              <span className="text-[11px] text-[#3F3F3F]">{s.desc}</span>
            </div>
          </li>
        ))}
      </ol>
    </Card>

    <CodeBlock title="Kong OIDC plugin" subtitle="Discovery URL dùng env thật; deploy per-route hoặc global qua Kong Admin API" code={KONG_PLUGIN_CONFIG} tone="text-[#F3E2A7]" />

    <Card title="Injected headers (backend contract)" subtitle="Kong đặt các header này sau khi verify JWT — backend đọc trực tiếp" padding="none">
      <table className="ui-table">
        <thead><tr><th>Header</th><th>JWT source</th><th>Example</th></tr></thead>
        <tbody>
          {INJECTED_HEADERS.map(h => (
            <tr key={h.header}>
              <td className="font-mono font-bold text-[#8A5A00] text-xs">{h.header}</td>
              <td className="text-xs text-[#3F3F3F]">{h.source}</td>
              <td className="font-mono text-xs">{h.example}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>

    <CodeBlock title="Backend — FastAPI trust pattern" subtitle="Backend KHÔNG verify lại JWT — chỉ đọc header của Kong" code={BACKEND_TRUST_CONFIG} tone="text-[#98C379]" />

    <Card title="JWKS" subtitle="Kong fetch JWKS của realm ở request đầu tiên và tự cache">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#FFF9E8] border border-[#BFA66A] rounded-xl mb-4">
        <span className="text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide shrink-0">{KC_REALM}</span>
        <span className="text-xs font-mono text-[#111111] truncate">{JWKS_URL}</span>
      </div>
      <EmptyState
        icon={Server}
        title="Không có nguồn dữ liệu — thống kê request/JWKS cache nằm ở Kong Admin API"
        description="Kong Admin API không được expose cho frontend. Dùng kong CLI hoặc Admin API nội bộ để xem trạng thái cache và lượt request."
      />
    </Card>

    <Card title="Network segmentation" subtitle="Thiết kế ngăn giả mạo X-User-Id / X-User-Roles khi bỏ qua Kong">
      <ul className="space-y-2">
        {NETWORK_SECURITY.map(item => (
          <li key={item.method} className="flex items-center gap-3 p-3.5 bg-white border border-[#BFA66A] rounded-xl">
            <span className="w-8 h-8 rounded-lg bg-[#E8F5E9] flex items-center justify-center shrink-0"><Lock className="w-4 h-4 text-[#1B5E20]" /></span>
            <div>
              <div className="text-sm font-bold text-[#111111]">{item.method}</div>
              <div className="text-[11px] text-[#3F3F3F]">{item.desc}</div>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-[#5F5F5F]">Đây là mô tả kiến trúc; trạng thái thực thi không được đọc từ hạ tầng.</p>
    </Card>
  </div>
);
