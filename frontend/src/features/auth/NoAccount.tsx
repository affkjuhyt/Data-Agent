import { Button } from '../../design-system/Button';
import keycloak from '../../lib/keycloak';
import { AuthScreen } from './AuthScreen';

/*
 * Signed in at Keycloak, but GET /v1/me answers 403: the organisation has no active account for
 * this person yet. Accounts come from the directory sync (UC-11-01), so there is nothing to fill
 * in here; the administrator adds the person to a group, or re-enables the account.
 */
export function NoAccount({ email, onRetry }: { email?: string; onRetry: () => void }) {
  return (
    <AuthScreen icon="clock" title="Chưa có tài khoản trong tổ chức">
      <p className="text-center text-muted-foreground da-body">
        Tài khoản{email ? ` ${email}` : ''} đã đăng nhập được nhưng chưa được kích hoạt trong Data Agent.
        Quản trị cần thêm bạn vào một nhóm trong thư mục của tổ chức; sau lần đồng bộ kế tiếp bạn
        dùng được ngay.
      </p>
      <div className="flex flex-col gap-da-sm">
        <Button icon="refresh" onClick={onRetry}>
          Kiểm tra lại
        </Button>
        <Button kind="secondary" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}>
          Đăng xuất
        </Button>
      </div>
    </AuthScreen>
  );
}
