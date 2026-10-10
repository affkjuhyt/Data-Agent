import { useEffect, useState, type FormEvent } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import keycloak from '../../lib/keycloak';
import { fetchAvailableRoles, fetchAvailableTenants, type TenantOption } from '../../lib/useMicrosoftApprovals';
import { AuthScreen } from './AuthScreen';

const ROLE_LABELS: Record<string, string> = {
  'ai-engineer': 'Kỹ sư AI',
  'executive-viewer': 'Lãnh đạo (chỉ xem)',
  'tenant-admin': 'Quản trị tổ chức',
  'data-analyst': 'Chuyên viên phân tích',
};

/* First Microsoft sign-in: the user asks for a role and an organisation; an admin approves. */
export function ProfileSetup({
  email,
  onSubmit,
}: {
  email?: string;
  onSubmit: (role: string, tenantId: string) => Promise<void>;
}) {
  const [roles, setRoles] = useState<string[]>([]);
  const [tenants, setTenants] = useState<TenantOption[] | null>(null);
  const [role, setRole] = useState('');
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAvailableRoles().then((list) => {
      setRoles(list);
      if (list.length > 0) setRole(list[0]);
    });
    fetchAvailableTenants().then((list) => {
      setTenants(list);
      if (list.length > 0) setTenantId(list[0].id);
    });
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!role || !tenantId) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(role, tenantId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không gửi được yêu cầu.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthScreen icon="shield" title="Hoàn tất hồ sơ">
      <p className="text-center text-muted-foreground da-body">
        Chọn vai trò và tổ chức để xin quyền truy cập{email ? ` cho ${email}` : ''}. Quản trị sẽ duyệt
        yêu cầu.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-da-lg">
        <fieldset className="flex flex-col gap-da-sm">
          <legend className="mb-da-sm text-foreground da-label">Vai trò</legend>
          {roles.map((r) => (
            <Radio key={r} name="role" value={r} checked={role === r} onChange={setRole}>
              {ROLE_LABELS[r] ?? r}
            </Radio>
          ))}
        </fieldset>
        {tenants === null ? (
          <p className="text-muted-foreground da-body-small">Đang tải danh sách tổ chức…</p>
        ) : (
          <Field label="Tổ chức">
            <Select
              label="Tổ chức"
              value={tenantId}
              onChange={setTenantId}
              options={tenants.map((t) => ({ value: t.id, label: t.name }))}
            />
          </Field>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" loading={submitting} disabled={!role || !tenantId}>
          Gửi yêu cầu truy cập
        </Button>
      </form>
      <Button kind="ghost" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}>
        Đăng xuất
      </Button>
    </AuthScreen>
  );
}
