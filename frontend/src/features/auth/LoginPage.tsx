import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import keycloak from '../../lib/keycloak';

/*
 * Figma "v3.1 · Đăng nhập [toàn cục]" (2230:14958). The app draws this page; the password never
 * reaches it. Microsoft Entra ID goes straight to the identity provider (kc_idp_hint), so the
 * Keycloak page is skipped; the AD account path opens Keycloak's own form (themed to match,
 * deploy/docker/keycloak/themes) for the password and the second factor.
 *
 * VITE_KEYCLOAK_ENTRA_IDP is the alias of the Entra identity provider in the realm; unset, the
 * Entra button is not drawn, because a hint to a missing or disabled provider ends on an error.
 * The Figma line "Phiên tự khoá sau 15 phút" is left out: the realm's idle timeout decides that,
 * and the page cannot read it before sign-in.
 */
const ENTRA_IDP = (import.meta.env.VITE_KEYCLOAK_ENTRA_IDP as string | undefined) || '';
const ENVIRONMENT = (import.meta.env.VITE_ENVIRONMENT_LABEL as string | undefined) || '';

export function LoginPage() {
  const redirectUri = window.location.href;
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-da-xl bg-card px-da-xl">
      <div className="flex w-full max-w-[420px] flex-col gap-da-xl rounded-lg border border-border bg-card p-da-3xl">
        <div className="flex items-center gap-[10px]">
          <span className="inline-flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Icon name="agent" />
          </span>
          <span className="text-[18px] font-semibold text-foreground">Data Agent</span>
        </div>
        <h1 className="text-[22px] font-semibold text-foreground">Đăng nhập</h1>
        <p className="text-muted-foreground da-body-small">
          Dùng tài khoản ngân hàng của bạn. Mật khẩu không lưu trong Data Agent.
        </p>
        {ENTRA_IDP && (
          <Button className="w-full" onClick={() => keycloak.login({ idpHint: ENTRA_IDP, redirectUri })}>
            Đăng nhập với Microsoft Entra ID
          </Button>
        )}
        <Button
          className="w-full"
          kind={ENTRA_IDP ? 'secondary' : 'primary'}
          onClick={() => keycloak.login({ redirectUri })}
        >
          Đăng nhập bằng tài khoản AD (LDAP)
        </Button>
        <div className="border-b border-border p-da-lg">
          <p className="text-foreground da-caption">Xác thực hai lớp theo chính sách của ngân hàng.</p>
        </div>
        <div className="flex flex-col gap-da-sm">
          <StatusDot tone="success">Triển khai tại chỗ</StatusDot>
          <p className="text-[11px] text-muted-foreground">
            Dữ liệu và mô hình AI chạy trong trung tâm dữ liệu của ngân hàng
          </p>
        </div>
      </div>
      <p className="text-center text-[11px] text-muted-foreground">
        Không đăng nhập được? Liên hệ Quản trị hệ thống · v3.1{ENVIRONMENT ? ` · môi trường ${ENVIRONMENT}` : ''}
      </p>
    </main>
  );
}
