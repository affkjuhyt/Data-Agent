import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import {
  inviteUser,
  listDataSpaceSummaries,
  type Role,
  type RowScope,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { EXPIRIES, expiryValue } from './format';
import { completeScope, RowScopePicker } from './RowScopePicker';
import { Dialog } from '../../design-system/Dialog';

/** "Hộp thoại · invite" (2095:8252): email, role, Data Space, row scope, expiry. */
export function InviteDialog({
  open,
  roles,
  onClose,
  onInvited,
}: {
  open: boolean;
  roles: Role[];
  onClose: () => void;
  onInvited: (email: string) => void;
}) {
  const spaces = useV1(['listDataSpaceSummaries', open], open ? listDataSpaceSummaries : null);
  const [email, setEmail] = useState('');
  const [roleId, setRoleId] = useState<string | null>(null);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [scope, setScope] = useState<RowScope | null>(null);
  const [expiry, setExpiry] = useState('none');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setEmail('');
    setRoleId(null);
    setSpaceId(null);
    setScope(null);
    setExpiry('none');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!email.trim() || !roleId || !spaceId) {
      setError('Nhập email, chọn vai trò và Data Space.');
      return;
    }
    if (!completeScope(scope)) {
      setError('Chọn ít nhất một giá trị cho phạm vi dòng, hoặc để toàn bộ dòng.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      await inviteUser({
        email: email.trim(),
        role_id: roleId,
        data_space_id: spaceId,
        row_scope: scope,
        valid_until: expiryValue(expiry),
      });
      onInvited(email.trim());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Mời người dùng"
      description="Người được mời đăng nhập qua SSO; quyền theo vai trò trong Data Space."
      submitLabel="Gửi lời mời"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Email">
        <Input
          type="email"
          value={email}
          placeholder="ten@congty.vn"
          onChange={(e) => setEmail(e.target.value)}
          className="w-full"
        />
      </Field>
      <Field label="Vai trò">
        <Select
          label="Vai trò"
          options={roles.map((r) => ({ value: r.id, label: r.name }))}
          value={roleId}
          onChange={setRoleId}
        />
      </Field>
      <Field label="Data Space">
        <Select
          label="Data Space"
          options={(spaces.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))}
          value={spaceId}
          onChange={(id) => {
            setSpaceId(id);
            setScope(null);
          }}
        />
      </Field>
      {spaceId ? (
        // A scope names values of this Data Space's tables, so it is chosen with the space.
        <RowScopePicker key={spaceId} spaceId={spaceId} value={scope} onChange={setScope} />
      ) : (
        <p className="text-muted-foreground da-caption">
          Chọn Data Space để đặt phạm vi dòng theo dữ liệu của nó.
        </p>
      )}
      <Field label="Hết hạn">
        <Select label="Hết hạn" options={EXPIRIES} value={expiry} onChange={setExpiry} />
      </Field>
    </Dialog>
  );
}
