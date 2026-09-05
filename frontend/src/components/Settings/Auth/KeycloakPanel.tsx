/**
 * Auth → Keycloak. There is no backend for realm inventory, federation bridges
 * or HA topology, so this panel shows only what is derived from the connected
 * realm (env) plus a persisted MFA policy record:
 *   live realm    VITE_KEYCLOAK_URL / _REALM / _CLIENT
 *   mfa policy    usePlatformSetting('mfa_policy')  Record<policyKey, boolean>
 *   role mappings documentation of the platform ↔ realm role contract
 */
import React from 'react';
import { Shield, ShieldCheck, ExternalLink, Copy, Check, Save, Server } from 'lucide-react';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState } from '../../ui';
import { usePlatformSetting } from '../../../lib/usePlatformSettings';

const KC_URL    = import.meta.env.VITE_KEYCLOAK_URL    ?? 'http://localhost:8110';
const KC_REALM  = import.meta.env.VITE_KEYCLOAK_REALM  ?? 'aeroflow';
const KC_CLIENT = import.meta.env.VITE_KEYCLOAK_CLIENT ?? 'aeroflow-frontend';
const KC_ADMIN_URL = `${KC_URL}/admin/master/console/#/${KC_REALM}`;

const LIVE_REALM = {
  realm: KC_REALM,
  base_url: KC_URL,
  client_id: KC_CLIENT,
  discovery: `${KC_URL}/realms/${KC_REALM}/.well-known/openid-configuration`,
  jwks_url: `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/certs`,
  token_endpoint: `${KC_URL}/realms/${KC_REALM}/protocol/openid-connect/token`,
};

/** Platform ↔ Keycloak role contract (documentation, mirrors IAM → Roles). */
const ROLE_MAPPINGS = [
  { platform_role: 'PLATFORM_ADMIN', kc_role: 'platform-admin' },
  { platform_role: 'AI_ENGINEER',    kc_role: 'ai-engineer' },
  { platform_role: 'EXECUTIVE',      kc_role: 'executive-viewer' },
];

/** MFA / session policies the platform expects in Keycloak. Enforcement lives in Keycloak; this records the intended policy. */
const MFA_POLICY_DEFS = [
  { key: 'totp',               label: 'TOTP (Authenticator App)', keycloak: 'Required Action: CONFIGURE_TOTP' },
  { key: 'webauthn',           label: 'WebAuthn / Passkey',       keycloak: 'Required Action: webauthn-register' },
  { key: 'session_limit',      label: 'Concurrent session limit', keycloak: 'Authentication → Session Limits' },
  { key: 'sso_idle',           label: 'SSO session idle timeout', keycloak: 'Realm settings → Sessions' },
  { key: 'backchannel_logout', label: 'Backchannel logout',       keycloak: 'Client → Backchannel logout URL' },
];
type MfaPolicy = Record<string, boolean>;

const CopyField = ({ label, value }: { label: string; value: string }) => {
  const [copied, setCopied] = React.useState(false);
  const copy = () => navigator.clipboard.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  return (
    <div>
      <div className="text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1">{label}</div>
      <div className="flex items-center gap-2 px-3 py-2 bg-[#FFF9E8] border border-[#BFA66A] rounded-xl">
        <span className="text-xs font-mono text-[#111111] truncate flex-1">{value}</span>
        <button type="button" onClick={copy} className="text-[#5F5F5F] hover:text-[#111111]" aria-label={`Copy ${label}`}>{copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}</button>
      </div>
    </div>
  );
};

const AdminLink = ({ path = '', children }: { path?: string; children: React.ReactNode }) => (
  <a href={`${KC_ADMIN_URL}${path}`} target="_blank" rel="noreferrer" className="btn-secondary btn-sm inline-flex items-center gap-1.5">
    <ExternalLink className="w-3.5 h-3.5" /> {children}
  </a>
);

export const KeycloakPanel = () => {
  const mfa = usePlatformSetting<MfaPolicy>('mfa_policy', {});
  const [draft, setDraft] = React.useState<MfaPolicy>({});
  const [dirty, setDirty] = React.useState(false);
  React.useEffect(() => { if (!mfa.loading) { setDraft(mfa.value ?? {}); setDirty(false); } }, [mfa.loading, mfa.value]);

  const toggle = (k: string) => { setDraft(d => ({ ...d, [k]: !d[k] })); setDirty(true); };
  const save = () => mfa.save(draft).then(() => setDirty(false)).catch(() => {});

  return (
    <div className="space-y-6">
      <Card
        title="Realm đang kết nối"
        subtitle="Suy ra từ VITE_KEYCLOAK_URL / VITE_KEYCLOAK_REALM / VITE_KEYCLOAK_CLIENT"
        actions={<div className="flex items-center gap-2"><Badge tone="healthy">connected</Badge><AdminLink>Keycloak Admin</AdminLink></div>}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <CopyField label="Realm" value={LIVE_REALM.realm} />
          <CopyField label="Client ID" value={LIVE_REALM.client_id} />
          <CopyField label="Base URL" value={LIVE_REALM.base_url} />
          <CopyField label="OIDC discovery" value={LIVE_REALM.discovery} />
          <CopyField label="JWKS URL" value={LIVE_REALM.jwks_url} />
          <CopyField label="Token endpoint" value={LIVE_REALM.token_endpoint} />
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card title="Realms & federation bridges" subtitle="Danh sách realm và identity provider (SAML/OIDC)">
          <EmptyState
            icon={Server}
            title="Không có nguồn dữ liệu — cấu hình trong Keycloak Admin"
            description="Nền tảng không đọc inventory realm hay identity broker từ Keycloak. Quản lý ở Identity Providers trong Admin Console."
            action={<AdminLink path="/identity-providers">Identity providers</AdminLink>}
          />
        </Card>
        <Card title="High availability" subtitle="Topology cụm Keycloak">
          <EmptyState
            icon={Server}
            title="Không có nguồn dữ liệu — cấu hình trong Keycloak Admin"
            description="Trạng thái node/cache Infinispan không được expose qua API của nền tảng. Kiểm tra ở Keycloak / hạ tầng."
            action={<AdminLink>Keycloak Admin</AdminLink>}
          />
        </Card>
      </div>

      <Card
        title="MFA & session policy"
        subtitle={<>Bản ghi chính sách mong muốn, lưu tại <code className="font-mono">platform/settings/mfa_policy</code>. Thực thi thực tế nằm trong Keycloak Authentication.</>}
        actions={<div className="flex gap-2"><AdminLink path="/authentication">Authentication</AdminLink><Button size="sm" variant="primary" icon={Save} loading={mfa.saving} disabled={!dirty} onClick={save}>Lưu</Button></div>}
      >
        {mfa.error && <div className="mb-4"><ErrorState message={mfa.error} onRetry={mfa.reload} /></div>}
        {mfa.loading ? <LoadingState /> : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {MFA_POLICY_DEFS.map(p => {
              const on = !!draft[p.key];
              return (
                <div key={p.key} className="flex items-center justify-between gap-3 p-3.5 bg-white border border-[#BFA66A] rounded-xl">
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-[#111111]">{p.label}</div>
                    <div className="text-[10px] text-[#5F5F5F] font-mono truncate">{p.keycloak}</div>
                  </div>
                  <button type="button" onClick={() => toggle(p.key)} aria-pressed={on} className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${on ? 'bg-[#B88719]' : 'bg-[#E4D6AE]'}`} aria-label={p.label}>
                    <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${on ? 'translate-x-5' : ''}`} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card title="Role mappings" subtitle="Platform role ↔ Keycloak realm role (hợp đồng cố định)" padding="none" actions={<AdminLink path="/roles">Realm roles</AdminLink>}>
        <table className="ui-table">
          <thead><tr><th>Platform role</th><th>Keycloak role</th></tr></thead>
          <tbody>
            {ROLE_MAPPINGS.map(m => (
              <tr key={m.kc_role}>
                <td className="font-bold text-xs"><span className="inline-flex items-center gap-1.5"><ShieldCheck className="w-3.5 h-3.5 text-[#8A5A00]" />{m.platform_role}</span></td>
                <td className="font-mono text-xs text-[#8A5A00]">{m.kc_role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="p-3 bg-[#FFF9E8] border border-[#BFA66A] rounded-xl flex items-start gap-2.5">
        <Shield className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
        <p className="text-[11px] text-[#3F3F3F]">Client secret không bao giờ được lưu trong nền tảng — tham chiếu OpenBao path qua Secrets Vault.</p>
      </div>
    </div>
  );
};
