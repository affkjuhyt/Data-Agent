import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
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
  listAdminUsers,
  listRoles,
  syncDirectory,
  type AdminUser,
  type Role,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { clock, count, dayMonth } from './format';
import { InviteDialog } from './InviteDialog';

/*
 * "P11 · Người dùng & vai trò" (2305:19433, UC-11-01 · UC-11-08): everyone with access, people
 * synced from the identity source and agents, with their effective role, Data Spaces, row
 * scope and expiry; a narrowed row scope is drawn in warning. On the right, on --background,
 * what each role may do. "Thêm vào Data Space" opens "Hộp thoại · invite".
 */
export default function UsersPage() {
  const toast = useToast();
  const users = useV1(listAdminUsers, []);
  const roles = useV1(listRoles, []);
  const [inviting, setInviting] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const people = users.data?.items.filter((u) => u.kind === 'person') ?? [];
  const lastSync = people.reduce<string | null>(
    (latest, u) =>
      u.last_synced_at && (!latest || u.last_synced_at > latest) ? u.last_synced_at : latest,
    null,
  );
  const summary = users.data
    ? `${count(people.length)} người từ ${people[0]?.identity_source ?? 'nguồn danh tính'} · đồng bộ ${clock(lastSync)} · vai trò gán theo nhóm AD, Data Space gán theo thành viên`
    : undefined;

  const sync = async () => {
    setSyncing(true);
    try {
      await syncDirectory();
      toast({ tone: 'success', message: 'Đã bắt đầu đồng bộ AD. Danh sách cập nhật khi xong.' });
      users.reload();
    } catch (e) {
      toast({ tone: 'error', message: `Không đồng bộ được: ${(e as Error).message}` });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Người dùng & vai trò']}
        title="Người dùng và vai trò"
        description={summary}
        actions={
          <>
            <Button loading={syncing} onClick={sync}>
              Đồng bộ AD
            </Button>
            <Button kind="primary" onClick={() => setInviting(true)}>
              Thêm vào Data Space
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg pl-da-3xl pr-da-2xl pt-da-xl pb-da-xl">
          <AsyncRegion
            state={users}
            what="người dùng"
            skeletonRows={5}
            isEmpty={(p) => p.items.length === 0}
            empty={
              <EmptyState
                icon="users"
                title="Chưa có người dùng"
                description="Người dùng được đồng bộ từ Active Directory. Bấm “Đồng bộ AD” để lấy danh sách."
              />
            }
          >
            {(p) => <UsersTable users={p.items} />}
          </AsyncRegion>
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Vai trò làm được gì</h2>
          <AsyncRegion state={roles} what="vai trò">
            {(p) => <RoleTable roles={p.items} />}
          </AsyncRegion>
          <p className="text-foreground da-body-small">
            Quản trị hệ thống không xem được dữ liệu nghiệp vụ. Agent chỉ có quyền trong giới hạn
            của người sở hữu agent.
          </p>
          <p className="text-muted-foreground da-caption">UC-11-01 · UC-11-08</p>
        </aside>
      </div>
      <InviteDialog
        open={inviting}
        roles={roles.data?.items ?? []}
        onClose={() => setInviting(false)}
        onInvited={(email) => {
          setInviting(false);
          toast({ tone: 'success', message: `Đã gửi lời mời tới ${email}` });
          users.reload();
        }}
      />
    </div>
  );
}

function UsersTable({ users }: { users: AdminUser[] }) {
  const view = useTableView(users, {
    search: (u) =>
      `${u.display_name} ${u.group_label} ${u.role_name} ${u.data_space_text} ${u.row_scope_text} ${dayMonth(u.valid_until)}`,
    filters: [{ id: 'role', label: 'Vai trò', value: (u) => u.role_name }],
    sorts: [
      { id: 'name', value: (u) => u.display_name },
      { id: 'role', value: (u) => u.role_name },
      { id: 'space', value: (u) => u.data_space_text },
      { id: 'scope', value: (u) => u.row_scope_text },
      { id: 'until', value: (u) => u.valid_until },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm người, vai trò…" searchLabel="Tìm người" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal">
              Người
            </SortableTh>
            <SortableTh
              view={view}
              column="role"
              className="w-[170px] px-da-lg py-da-md font-normal"
            >
              Vai trò
            </SortableTh>
            <SortableTh
              view={view}
              column="space"
              className="w-[200px] px-da-lg py-da-md font-normal"
            >
              Data Space
            </SortableTh>
            <SortableTh
              view={view}
              column="scope"
              className="w-[170px] px-da-lg py-da-md font-normal"
            >
              Phạm vi dòng
            </SortableTh>
            <SortableTh
              view={view}
              column="until"
              className="w-[90px] px-da-lg py-da-md font-normal"
            >
              Hạn
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((u) => (
            <tr key={u.id} className="border-b border-border align-middle">
              <td className="px-da-lg py-da-md">
                <div className="flex min-w-0 flex-col gap-[2px]">
                  <span className="truncate text-foreground da-body-strong">{u.display_name}</span>
                  <span className="truncate text-muted-foreground da-mono">{u.group_label}</span>
                </div>
              </td>
              <td className="px-da-lg py-da-md text-foreground da-body-small">{u.role_name}</td>
              <td className="px-da-lg py-da-md text-foreground da-body-small">
                {u.data_space_text}
              </td>
              <td
                className={cn(
                  'px-da-lg py-da-md da-body-small',
                  u.row_scope_limited ? 'text-warning' : 'text-foreground',
                )}
              >
                {u.row_scope_text}
              </td>
              <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                {dayMonth(u.valid_until)}
              </td>
            </tr>
          ))}
          {view.total === 0 && tableEmptyRow(5)}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}

const ROLE_COLUMNS = [
  { key: 'ask', label: 'Hỏi', width: 'w-[40px]' },
  { key: 'approve', label: 'Duyệt', width: 'w-[44px]' },
  { key: 'admin', label: 'Quản trị', width: 'w-[56px]' },
];

function RoleTable({ roles }: { roles: Role[] }) {
  const can = (role: Role, key: string) => role.capabilities.includes(key);
  const view = useTableView(roles, {
    search: (r) => r.name,
    filters: [{ id: 'admin', label: 'Quản trị', value: (r) => (can(r, 'admin') ? 'Có' : 'Không') }],
    sorts: [
      { id: 'name', value: (r) => r.name },
      ...ROLE_COLUMNS.map((c) => ({ id: c.key, value: (r: Role) => (can(r, c.key) ? 1 : 0) })),
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm vai trò…" searchLabel="Tìm vai trò" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal">
              Vai trò
            </SortableTh>
            {ROLE_COLUMNS.map((c) => (
              <SortableTh
                key={c.key}
                view={view}
                column={c.key}
                className={cn(c.width, 'box-content px-da-xs py-da-md font-normal')}
              >
                {c.label}
              </SortableTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr key={r.id} className="border-b border-border text-foreground da-body-small">
              <td className="px-da-lg py-da-md">{r.name}</td>
              {ROLE_COLUMNS.map((c) => {
                const can = r.capabilities.includes(c.key);
                return (
                  <td key={c.key} className="px-da-xs py-da-md" aria-label={can ? 'Có' : 'Không'}>
                    {can ? '✓' : '—'}
                  </td>
                );
              })}
            </tr>
          ))}
          {view.total === 0 && tableEmptyRow(1 + ROLE_COLUMNS.length)}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}
