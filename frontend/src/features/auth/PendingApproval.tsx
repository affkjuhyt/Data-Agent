import { Button } from '../../design-system/Button';
import keycloak from '../../lib/keycloak';
import { AuthScreen } from './AuthScreen';

/* A Microsoft account that signed in but is not approved yet. */
export function PendingApproval({
  email,
  since,
  onRetry,
}: {
  email?: string;
  since?: string;
  onRetry: () => void;
}) {
  const sinceLabel = since ? new Date(since).toLocaleDateString('vi-VN') : null;
  return (
    <AuthScreen icon="clock" title="Đang chờ quản trị duyệt">
      <div className="flex flex-col gap-da-sm text-center">
        <p className="text-muted-foreground da-body">
          Tài khoản Microsoft{email ? ` ${email}` : ''} đã được ghi nhận. Quản trị cần duyệt trước khi bạn
          dùng được Data Agent.
        </p>
        {sinceLabel && <p className="text-muted-foreground da-caption">Gửi yêu cầu ngày {sinceLabel}</p>}
      </div>
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
