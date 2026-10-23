import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { listDataSpaceSummaries, type DataSpaceSummary } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { count } from './format';
import DataSpaceMembers from './DataSpaceMembers';

/*
 * Rail item "Data Space". With an id (/admin/data_spaces/<id>) it is "v3.1 · Cộng tác · Thành
 * viên & quyền Data Space" (2084:6445). Without one it lists the Data Spaces to pick from:
 * Figma has no list frame, so it uses the P11 table idiom (bordered header, 12/8 rows).
 */
const STATUS: Record<
  DataSpaceSummary['status'],
  { tone: 'success' | 'warning' | 'idle' | 'neutral'; label: string }
> = {
  active: { tone: 'success', label: 'Đang dùng' },
  onboarding: { tone: 'warning', label: 'Đang thiết lập' },
  draft: { tone: 'idle', label: 'Nháp' },
  read_only: { tone: 'neutral', label: 'Chỉ đọc' },
  archived: { tone: 'idle', label: 'Đã lưu trữ' },
};

export default function DataSpacesPage() {
  const { routeId } = useAppState();
  return routeId ? <DataSpaceMembers id={routeId} /> : <DataSpaceList />;
}

function DataSpaceList() {
  const { navigate } = useAppState();
  const spaces = useV1(['listDataSpaceSummaries'], listDataSpaceSummaries);
  const items = spaces.data?.items ?? [];
  const view = useTableView(items, {
    search: (s) =>
      `${s.name} ${s.code} ${s.steward_name} ${s.member_count ?? ''} ${STATUS[s.status].label}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (s) => STATUS[s.status].label }],
    sorts: [
      { id: 'name', value: (s) => s.name },
      { id: 'steward', value: (s) => s.steward_name },
      { id: 'members', value: (s) => s.member_count },
      { id: 'status', value: (s) => STATUS[s.status].label },
    ],
    pageSize: 50,
  });

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Data Space']}
        title="Data Space"
        description={
          spaces.data
            ? `${items.length} Data Space · mỗi vùng có nguồn, chỉ số, agent và quyền riêng`
            : undefined
        }
        actions={
          <Button kind="primary" onClick={() => navigate('admin', 'DATA_SPACE_NEW')}>
            Tạo Data Space
          </Button>
        }
      />
      <div className="px-da-3xl pt-da-xl">
        <AsyncRegion
          state={spaces}
          what="Data Space"
          isEmpty={(p) => p.items.length === 0}
          empty={
            <EmptyState
              icon="layers"
              title="Chưa có Data Space nào"
              description="Data Space là vùng dữ liệu riêng của một nhóm nghiệp vụ: nguồn, chỉ số, agent và quyền."
              action={
                <Button kind="primary" onClick={() => navigate('admin', 'DATA_SPACE_NEW')}>
                  Tạo Data Space
                </Button>
              }
            />
          }
        >
          {() => (
            <div className="flex flex-col gap-da-lg">
              <TableToolbar
                view={view}
                placeholder="Tìm Data Space…"
                searchLabel="Tìm Data Space"
              />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal">
                      Data Space
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="steward"
                      className="w-[200px] px-da-lg py-da-md font-normal"
                    >
                      Người phụ trách
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="members"
                      className="w-[120px] px-da-lg py-da-md font-normal"
                    >
                      Thành viên
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="status"
                      className="w-[150px] px-da-lg py-da-md font-normal"
                    >
                      Trạng thái
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((s) => (
                    <tr key={s.id} className="border-b border-border align-middle hover:bg-subtle">
                      <td className="px-da-lg py-da-md">
                        <button
                          type="button"
                          className="flex min-w-0 flex-col gap-[2px] text-left"
                          onClick={() => navigate('admin', 'DATA_SPACES', s.id)}
                        >
                          <span className="truncate text-foreground da-body-strong">{s.name}</span>
                          <span className="truncate text-muted-foreground da-mono">{s.code}</span>
                        </button>
                      </td>
                      <td className="px-da-lg py-da-md text-foreground da-body-small">
                        {s.steward_name}
                      </td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                        {s.member_count === null ? '—' : count(s.member_count)}
                      </td>
                      <td className="px-da-lg py-da-md">
                        <StatusDot tone={STATUS[s.status].tone}>{STATUS[s.status].label}</StatusDot>
                      </td>
                    </tr>
                  ))}
                  {view.total === 0 && tableEmptyRow(4)}
                </tbody>
              </table>
              <TablePager view={view} />
            </div>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}
