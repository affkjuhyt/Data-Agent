import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import {
  listAdminUsers,
  listGroups,
  putMembers,
  type DataSpaceMember,
  type MemberInput,
  type RowScope,
  type PermissionMatrix,
} from '../../lib/v1/adminAccess';
import { useV1, useV1Mutation } from '../../lib/v1/useV1';
import { completeScope, RowScopePicker } from './RowScopePicker';
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
  const groups = useV1(['listGroups', open], open ? listGroups : null);
  const users = useV1(['listAdminUsers', open], open ? listAdminUsers : null);
  const [principal, setPrincipal] = useState<string | null>(null);
  const [roleId, setRoleId] = useState<string | null>(null);
  const [scope, setScope] = useState<RowScope | null>(null);
  const [invalid, setInvalid] = useState<string>();
  const add = useV1Mutation(
    ({ list }: { list: MemberInput[]; who: string }) => putMembers(dataSpaceId, list),
    {
      invalidates: [
        ['listMembers', dataSpaceId],
        ['getDataSpace', dataSpaceId],
      ],
      onSuccess: (_, { who }) => onAdded(who),
    },
  );

  useEffect(() => {
    if (!open) return;
    setPrincipal(null);
    setRoleId(null);
    setScope(null);
    setInvalid(undefined);
    add.reset();
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

  const refuse = (message: string) => {
    add.reset();
    setInvalid(message);
  };

  const submit = () => {
    if (!principal || !roleId) {
      refuse('Chọn người hoặc nhóm và vai trò.');
      return;
    }
    if (!completeScope(scope)) {
      refuse('Chọn ít nhất một giá trị cho phạm vi dòng, hoặc để toàn bộ dòng.');
      return;
    }
    const [type, principalId] = principal.split(':') as ['user' | 'group', string];
    const current: MemberInput[] = members.map((m) => ({
      principal_type: m.principal_type,
      principal_id: m.principal_id,
      role_id: m.role_id,
      row_scope: m.row_scope,
      valid_until: m.valid_until,
    }));
    setInvalid(undefined);
    add.run({
      list: [
        ...current,
        {
          principal_type: type,
          principal_id: principalId,
          role_id: roleId,
          row_scope: scope,
          valid_until: null,
        },
      ],
      who: options.find((o) => o.value === principal)?.label.split(' · ')[0] ?? principalId,
    });
  };

  return (
    <Dialog
      open={open}
      title="Thêm thành viên Data Space"
      description="Quyền theo vai trò; agent luôn theo quyền của chủ sở hữu."
      submitLabel="Thêm"
      submitting={add.pending}
      error={invalid ?? add.error?.message}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Người hoặc nhóm">
        <Select
          label="Người hoặc nhóm"
          options={options}
          value={principal}
          onChange={setPrincipal}
        />
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
      <RowScopePicker spaceId={dataSpaceId} value={scope} onChange={setScope} />
    </Dialog>
  );
}
