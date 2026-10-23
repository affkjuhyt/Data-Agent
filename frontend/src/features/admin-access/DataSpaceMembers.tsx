import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AgentChip } from '../../design-system/AgentChip';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Avatar } from '../../design-system/Avatar';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  CAPABILITIES,
  getDataSpace,
  getPermissions,
  listAccessRequests,
  listMembers,
  type DataSpaceMember,
  type PermissionCell,
  type PermissionMatrix,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { count, dayMonth } from './format';
import { MemberAddDialog } from './MemberAddDialog';

/*
 * "v3.1 · Cộng tác · Thành viên & quyền Data Space" (2084:6445, UC-11-08): the role × capability
 * matrix in effect (✓, "cần duyệt", —, "không bao giờ"), the row/column/agent rules under it,
 * and the members on the right with the pending access requests of this Data Space.
 * "Mô phỏng quyền" opens "Cùng bảng dưới hai vai trò"; "Thêm thành viên" opens memberAdd.
 */
export default function DataSpaceMembers({ id }: { id: string }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const space = useV1(['getDataSpace', id], () => getDataSpace(id));
  const matrix = useV1(['getPermissions', id], () => getPermissions(id));
  const members = useV1(['listMembers', id], () => listMembers(id));
  const pending = useV1(['listAccessRequests', { status: 'pending', data_space_id: id }, id], () =>
    listAccessRequests({ status: 'pending', data_space_id: id }),
  );
  const [adding, setAdding] = useState(false);

  const name = space.data?.name;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Data Space']}
        title={name ? `${name} · thành viên và quyền` : 'Thành viên và quyền'}
        actions={
          <>
            <Button icon="eye" onClick={() => navigate('admin', 'PERMISSION_COMPARE', id)}>
              Mô phỏng quyền
            </Button>
            <Button kind="primary" onClick={() => setAdding(true)}>
              Thêm thành viên
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-2xl py-da-xl">
          <AsyncRegion state={matrix} what="ma trận quyền" skeletonRows={5}>
            {(m) => (
              <>
                <MatrixTable matrix={m} />
                <div className="flex gap-da-md max-[1279px]:flex-col">
                  {[
                    { title: 'Theo dòng', text: m.rules.row },
                    { title: 'Theo cột', text: m.rules.column },
                    { title: 'Agent', text: m.rules.agent },
                  ].map((rule) => (
                    <div
                      key={rule.title}
                      className="flex min-w-0 flex-1 flex-col gap-da-xs rounded-md border border-border bg-card px-da-lg py-da-md"
                    >
                      <span className="text-foreground da-body-strong">{rule.title}</span>
                      <span className="text-muted-foreground da-caption">{rule.text}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </AsyncRegion>
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-da-md border-l border-border bg-card px-da-xl py-da-lg max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">
            Thành viên
            {space.data?.member_count != null ? ` · ${count(space.data.member_count)}` : ''}
          </h2>
          <AsyncRegion state={members} what="thành viên">
            {(p) => (
              <ul className="flex flex-col gap-da-md">
                {p.items.map((m) => (
                  <MemberRow key={m.id} member={m} />
                ))}
              </ul>
            )}
          </AsyncRegion>
          {pending.data && pending.data.items.length > 0 && (
            <button
              type="button"
              onClick={() => navigate('admin', 'ACCESS_REQUESTS')}
              className="flex items-start gap-da-sm rounded-md border border-border px-da-md py-da-sm text-left hover:bg-subtle"
            >
              <Icon name="lock" className="text-warning" />
              <span className="flex-1 text-foreground da-caption">
                {pending.data.items.length} yêu cầu quyền đang chờ:{' '}
                {pending.data.items.map((r) => r.queue_label).join(', ')}
              </span>
            </button>
          )}
        </aside>
      </div>
      <MemberAddDialog
        open={adding}
        dataSpaceId={id}
        roles={matrix.data?.roles ?? []}
        members={members.data?.items ?? []}
        onClose={() => setAdding(false)}
        onAdded={(who) => {
          setAdding(false);
          toast({ tone: 'success', message: `Đã thêm ${who} vào ${name ?? 'Data Space'}` });
          members.reload();
          space.reload();
        }}
      />
    </div>
  );
}

function cellLabel(value: PermissionCell): string {
  if (value === 'yes') return 'Được';
  if (value === 'approval') return 'cần duyệt';
  if (value === 'never') return 'không bao giờ';
  return '—';
}

function Cell({ value }: { value: PermissionCell }) {
  if (value === 'yes')
    return (
      <span className="inline-flex text-success">
        <Icon name="check" label="Được" />
      </span>
    );
  if (value === 'approval') return <StatusDot tone="warning">cần duyệt</StatusDot>;
  if (value === 'never')
    return <span className="text-muted-foreground da-caption">không bao giờ</span>;
  return (
    <span className="text-muted-foreground da-body-small" aria-label="Không">
      —
    </span>
  );
}

function MatrixTable({ matrix }: { matrix: PermissionMatrix }) {
  const view = useTableView(matrix.roles, {
    search: (r) =>
      `${r.name} ${r.member_count} ${r.member_unit} ${CAPABILITIES.map((c) => cellLabel(r.cells[c.key])).join(' ')}`,
    filters: [{ id: 'unit', label: 'Thành viên', value: (r) => r.member_unit }],
    sorts: [
      { id: 'role', value: (r) => r.name },
      ...CAPABILITIES.map((c) => ({
        id: c.key,
        value: (r: PermissionMatrix['roles'][number]) => cellLabel(r.cells[c.key]),
      })),
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm vai trò…" searchLabel="Tìm vai trò" />
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-subtle text-muted-foreground da-label">
              <SortableTh
                view={view}
                column="role"
                className="w-[200px] px-da-lg py-da-sm text-left font-medium"
              >
                Vai trò
              </SortableTh>
              {CAPABILITIES.map((c) => (
                <SortableTh
                  key={c.key}
                  view={view}
                  column={c.key}
                  className="whitespace-nowrap px-da-xs py-da-sm text-center font-medium"
                >
                  {c.label}
                </SortableTh>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((r) => (
              <tr key={r.role_id} className="border-t border-border">
                <th scope="row" className="px-da-lg py-da-sm text-left font-normal">
                  <span className="block text-foreground da-body-strong">{r.name}</span>
                  <span className="block text-muted-foreground da-caption">
                    {r.member_count} {r.member_unit}
                  </span>
                </th>
                {CAPABILITIES.map((c) => (
                  <td key={c.key} className="px-da-xs py-da-sm text-center">
                    <Cell value={r.cells[c.key]} />
                  </td>
                ))}
              </tr>
            ))}
            {view.total === 0 && tableEmptyRow(1 + CAPABILITIES.length)}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </>
  );
}

function MemberRow({ member }: { member: DataSpaceMember }) {
  const [open, setOpen] = useState(false);
  // A temporary grant shows its expiry where a permanent one shows its row scope.
  const detail = [
    member.role_name,
    member.valid_until
      ? `Hết hạn ${dayMonth(member.valid_until)}`
      : (member.row_scope_text ?? 'Toàn bộ'),
    member.access_note,
  ]
    .filter(Boolean)
    .join(' · ');
  const source = {
    group_mapping: 'Gán theo nhóm AD',
    manual: 'Thêm trực tiếp',
    access_request: 'Cấp từ yêu cầu quyền, tự thu hồi khi hết hạn',
  }[member.source];

  return (
    <li className="flex flex-col gap-da-xs">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-da-md text-left"
      >
        {member.principal_type === 'agent' ? (
          <AgentChip name={member.display_name} status={member.agent_status ?? 'idle'} />
        ) : (
          <>
            <Avatar name={member.display_name} />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-foreground da-body-strong">{member.display_name}</span>
              <span className="text-muted-foreground da-caption">{detail}</span>
            </span>
          </>
        )}
        <span className="flex-1" />
        <Icon
          name="chevron-down"
          className={cn('text-foreground transition-transform', open && 'rotate-180')}
        />
      </button>
      {open && <p className="pl-[36px] text-muted-foreground da-caption">{source}</p>}
    </li>
  );
}
