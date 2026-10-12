import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import {
  listAdminUsers,
  listGroups,
  putMembers,
  type DataSpaceMember,
  type MemberInput,
  type PermissionMatrix,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { ROW_SCOPES, scopeValue } from './format';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · memberAdd" (2095:9350): a person or an AD group, a role, a row scope. A2 only has
 * PUT /v1/data-spaces/{id}/members, so the dialog sends the current list plus the new member.
 */
export function MemberAddDialog({
  open,
  dataSpaceId,
  roles,
  members,
  onClose,
  onAdded,
}: {
  open: boolean;
  dataSpaceId: string;
  roles: PermissionMatrix['roles'];
  members: DataSpaceMember[];
  onClose: () => void;
  onAdded: (who: string) => void;
}) {
  const groups = useV1(open ? listGroups : null, [open]);
  const users = useV1(open ? listAdminUsers : null, [open]);
  const [principal, setPrincipal] = useState<string | null>(null);
  const [roleId, setRoleId] = useState<string | null>(null);
  const [scope, setScope] = useState('Theo vùng phụ trách');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setPrincipal(null);
    setRoleId(null);
    setScope('Theo vùng phụ trách');
    setError(undefined);
  }, [open]);

  // Agents join a Data Space through their owner, so only people and groups are offered.
  const options = [
    ...(groups.data?.items ?? []).map((g) => ({
      value: `group:${g.id}`,
      label: `${g.name} · ${g.member_count} người`,
    })),
    ...(users.data?.items ?? [])
      .filter((u) => u.kind === 'person')
      .map((u) => ({ value: `user:${u.id}`, label: u.display_name })),
  ];

  const submit = async () => {
    if (!principal || !roleId) {
      setError('Chọn người hoặc nhóm và vai trò.');
      return;
    }
    const [type, principalId] = principal.split(':') as ['user' | 'group', string];
    const current: MemberInput[] = members.map((m) => ({
      principal_type: m.principal_type,
      principal_id: m.principal_id,
      role_id: m.role_id,
      row_scope: m.row_scope_text,
      valid_until: m.valid_until,
    }));
    setSending(true);
    setError(undefined);
    try {
      await putMembers(dataSpaceId, [
        ...current,
        {
          principal_type: type,
          principal_id: principalId,
          role_id: roleId,
          row_scope: scopeValue(scope),
          valid_until: null,
        },
      ]);
      onAdded(options.find((o) => o.value === principal)?.label.split(' · ')[0] ?? principalId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Thêm thành viên Data Space"
      description="Quyền theo vai trò; agent luôn theo quyền của chủ sở hữu."
      submitLabel="Thêm"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Người hoặc nhóm">
        <Select label="Người hoặc nhóm" options={options} value={principal} onChange={setPrincipal} />
      </Field>
      <Field label="Vai trò">
        <Select
          label="Vai trò"
          options={roles
            .filter((r) => r.member_unit === 'người')
            .map((r) => ({ value: r.role_id, label: r.name }))}
          value={roleId}
          onChange={setRoleId}
        />
      </Field>
      <Field label="Phạm vi dòng">
        <Select label="Phạm vi dòng" options={ROW_SCOPES} value={scope} onChange={setScope} />
      </Field>
    </Dialog>
  );
}
