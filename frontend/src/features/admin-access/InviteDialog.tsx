import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { inviteUser, listDataSpaceSummaries, type Role } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { EXPIRIES, ROW_SCOPES, expiryValue, scopeValue } from './format';
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
  const spaces = useV1(open ? listDataSpaceSummaries : null, [open]);
  const [email, setEmail] = useState('');
  const [roleId, setRoleId] = useState<string | null>(null);
  const [spaceId, setSpaceId] = useState<string | null>(null);
  const [scope, setScope] = useState('all');
  const [expiry, setExpiry] = useState('none');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setEmail('');
    setRoleId(null);
    setSpaceId(null);
    setScope('all');
    setExpiry('none');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!email.trim() || !roleId || !spaceId) {
      setError('Nhập email, chọn vai trò và Data Space.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      await inviteUser({
        email: email.trim(),
        role_id: roleId,
        data_space_id: spaceId,
        row_scope: scopeValue(scope),
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
          onChange={setSpaceId}
        />
      </Field>
      <Field label="Phạm vi dòng">
        <Select label="Phạm vi dòng" options={ROW_SCOPES} value={scope} onChange={setScope} />
      </Field>
      <Field label="Hết hạn">
        <Select label="Hết hạn" options={EXPIRIES} value={expiry} onChange={setExpiry} />
      </Field>
    </Dialog>
  );
}
