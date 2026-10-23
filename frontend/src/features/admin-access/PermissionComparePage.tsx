import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Avatar } from '../../design-system/Avatar';
import { Button } from '../../design-system/Button';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  requestAccess,
  simulatePermissions,
  type AccessCapability,
  type PermissionView,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { addDays, isoDay } from './format';

/*
 * "v3.1 · Cộng tác · Cùng bảng dưới hai vai trò" (2084:6905, UC-11-02/08), opened from
 * "Mô phỏng quyền" of a Data Space: one table as two roles see it (POST /v1/permissions:simulate).
 * Masked or aggregated cells are muted; an action the role may not take stays visible, greyed,
 * with the reason in a tooltip, and clicking it sends an access request.
 */
export default function PermissionComparePage() {
  const { routeId } = useAppState();
  const sim = useV1(
    ['simulatePermissions', routeId],
    routeId ? () => simulatePermissions({ data_space_id: routeId }) : null,
  );

  return (
    <div className="flex flex-col gap-da-md px-da-2xl py-da-xl">
      <PageHeader
        breadcrumb={['Quản trị', 'Data Space', 'Mô phỏng quyền']}
        title={sim.data ? `Cùng bảng ${sim.data.table} · hai vai trò` : 'Cùng bảng · hai vai trò'}
        description="Giao diện thay đổi theo quyền: cột bị che, dòng bị giới hạn, hành động không được phép hiển thị lý do thay vì ẩn đi."
      />
      <AsyncRegion state={sim} what="mô phỏng quyền" skeletonRows={4}>
        {(s) => (
          <div className="grid grid-cols-2 gap-da-md max-[1023px]:grid-cols-1">
            {s.views.map((view) => (
              <ViewCard key={view.principal_name} view={view} dataSpaceId={routeId!} />
            ))}
          </div>
        )}
      </AsyncRegion>
    </div>
  );
}

type SimulatedRow = { cells: string[]; muted: boolean[] };

function CompareTable({ view }: { view: PermissionView }) {
  const source: SimulatedRow[] = view.rows.map((cells, i) => ({
    cells,
    muted: view.muted[i] ?? [],
  }));
  const table = useTableView(source, {
    search: (row) => row.cells.join(' '),
    filters: [
      {
        id: 'col0',
        label: view.columns[0] ?? 'Cột',
        value: (row) => row.cells[0] ?? '',
      },
    ],
    sorts: view.columns.map((_, index) => ({
      id: `c${index}`,
      value: (row: SimulatedRow) => row.cells[index] ?? '',
    })),
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={table} placeholder="Tìm trong bảng…" searchLabel="Tìm trong bảng" />
      <table className="w-full table-fixed border-collapse border-b border-border">
        <thead>
          <tr className="bg-subtle text-left text-muted-foreground da-mono">
            {view.columns.map((c, i) => (
              <SortableTh
                key={`${c}-${i}`}
                view={table}
                column={`c${i}`}
                className="px-da-md py-da-sm font-normal"
              >
                {c}
              </SortableTh>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i} className="border-t border-border da-mono">
              {row.cells.map((cell, j) => (
                <td
                  key={j}
                  className={cn(
                    'truncate px-da-md py-da-sm',
                    row.muted[j] ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
          {table.total === 0 && tableEmptyRow(Math.max(view.columns.length, 1))}
        </tbody>
      </table>
      <TablePager view={table} />
    </div>
  );
}

function ViewCard({ view, dataSpaceId }: { view: PermissionView; dataSpaceId: string }) {
  const toast = useToast();
  const [asked, setAsked] = useState<string[]>([]);

  const ask = async (label: string, capability: string | undefined) => {
    if (asked.includes(label)) return;
    try {
      await requestAccess({
        data_space_id: dataSpaceId,
        capability: (capability ?? 'view_detail') as AccessCapability,
        reason: `Cần quyền để “${label}”`,
        requested_until: isoDay(addDays(30)),
      });
      setAsked((list) => [...list, label]);
      toast({ tone: 'success', message: 'Đã gửi yêu cầu quyền tới người phụ trách dữ liệu.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không gửi được yêu cầu quyền: ${(e as Error).message}` });
    }
  };

  return (
    <article
      aria-label={`${view.role_name} · ${view.principal_name}`}
      className="flex min-w-0 flex-col gap-da-md rounded-lg border border-border bg-card p-da-lg"
    >
      <div className="flex items-center gap-da-sm">
        <Avatar name={view.principal_name} />
        <h2 className="flex-1 text-foreground da-body-strong">
          {view.role_name} · {view.principal_name}
        </h2>
      </div>
      <StatusDot tone="primary">{view.scope_text}</StatusDot>
      <CompareTable view={view} />
      <div className="flex items-center gap-da-sm">
        {view.actions.map((action, i) =>
          action.allowed ? (
            <Button key={action.label} kind={i === 0 ? 'primary' : 'secondary'}>
              {action.label}
            </Button>
          ) : (
            <Tooltip key={action.label} content={action.reason}>
              {/* A disabled Button swallows its own click but not the bubbling one: the wrapper sends the request. */}
              <span onClick={() => ask(action.label, action.request_capability)}>
                <Button disabled>{action.label}</Button>
              </span>
            </Tooltip>
          ),
        )}
      </div>
    </article>
  );
}
