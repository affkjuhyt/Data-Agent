import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatNumber, formatTime } from '../../lib/formatVN';
import { getSource, testSource, type SourceDetail } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { dataSpacesText, freshnessText, modeLabel, sourceTone } from './sourceFormat';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * One source (/data/source/<id>): what it is, its health, and its tables; a table opens
 * "P04 · Nguồn · Hồ sơ bảng". Figma has no frame for this level — it is composed from the
 * list's header and table idiom so the breadcrumb "Dữ liệu / Nguồn / Core banking / LD_LOANS"
 * has a page at each step.
 */
export default function SourceDetailPage() {
  const { routeId } = useAppState();
  const source = useV1(['getSource', routeId], routeId ? () => getSource(routeId) : null);
  // A source in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(source.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={source} what="nguồn" skeletonRows={6}>
      {(data) => <SourceView source={data} reload={source.reload} />}
    </AsyncRegion>
  );
}

function SourceView({ source, reload }: { source: SourceDetail; reload: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const check = async () => {
    try {
      const result = await testSource(source.id);
      const failed = result.checks.filter((c) => !c.ok);
      toast(
        failed.length === 0
          ? {
              tone: 'success',
              message: `${result.checks.length}/${result.checks.length} kiểm tra đạt.`,
            }
          : { tone: 'error', message: `${failed[0].title} — không đạt. ${failed[0].detail}` },
      );
      reload();
    } catch (e) {
      toast({ tone: 'error', message: `Không kiểm tra được kết nối. ${(e as Error).message}` });
    }
  };
  const tone = sourceTone(source);
  const view = useTableView(source.tables, {
    search: (table) =>
      `${table.name} ${table.description ?? ''} ${
        table.row_count === null ? '' : formatNumber(table.row_count)
      } ${table.column_count ?? ''} ${table.refreshed_at ? formatTime(table.refreshed_at) : ''}`,
    filters: [
      { id: 'description', label: 'Mô tả', value: (table) => table.description ?? 'Không mô tả' },
    ],
    sorts: [
      { id: 'name', value: (table) => table.name },
      { id: 'rows', value: (table) => table.row_count },
      { id: 'columns', value: (table) => table.column_count },
      { id: 'refreshed', value: (table) => table.refreshed_at },
    ],
    pageSize: 50,
  });

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Nguồn', source.name]}
        title={source.name}
        description={`${source.connector} · ${source.endpoint} · ${modeLabel(source)} · dữ liệu mới nhất ${freshnessText(source)} · ${dataSpacesText(source)}`}
        actions={
          <>
            {source.connector === 'rest' && (
              <Button kind="ghost" onClick={() => navigate('data', 'CONNECT_API', source.id)}>
                Sửa kết nối API
              </Button>
            )}
            <Button onClick={() => void check()}>Kiểm tra kết nối</Button>
            {source.drift_id && (
              <Button kind="primary" onClick={() => navigate('data', 'DRIFT', source.drift_id!)}>
                Duyệt thay đổi cấu trúc
              </Button>
            )}
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        <div className="flex items-center gap-da-md">
          <StatusDot tone={tone}>{source.status_label}</StatusDot>
        </div>
        {source.status_reason && (tone === 'error' || tone === 'warning') && (
          <Notice tone={tone}>{source.status_reason}</Notice>
        )}
        {source.tables.length === 0 ? (
          <EmptyState
            icon="table"
            title="Chưa có bảng nào"
            description="Bảng hiện ở đây sau khi nguồn được kiểm tra và bạn chọn dữ liệu để dùng."
          />
        ) : (
          <>
          <TableToolbar view={view} placeholder="Tìm bảng…" searchLabel="Tìm bảng" />
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal">
                  Bảng
                </SortableTh>
                <SortableTh view={view} column="rows" className="w-[140px] px-da-md py-da-md font-normal">
                  Dòng
                </SortableTh>
                <SortableTh view={view} column="columns" className="w-[100px] px-da-md py-da-md font-normal">
                  Cột
                </SortableTh>
                <SortableTh view={view} column="refreshed" className="w-[120px] px-da-md py-da-md font-normal">
                  Cập nhật
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((table) => (
                <tr
                  key={table.id}
                  tabIndex={0}
                  onClick={() => navigate('data', 'TABLE', table.id)}
                  onKeyDown={(e) => e.key === 'Enter' && navigate('data', 'TABLE', table.id)}
                  className="cursor-pointer border-b border-border hover:bg-subtle focus-visible:bg-subtle"
                >
                  <td className="px-da-lg py-[10px]">
                    <div className="flex flex-col gap-[2px]">
                      <span className="text-foreground da-mono">{table.name}</span>
                      {table.description && (
                        <span className="text-muted-foreground da-caption">
                          {table.description}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-da-md py-[10px] text-foreground da-mono">
                    {table.row_count === null ? '—' : formatNumber(table.row_count)}
                  </td>
                  <td className="px-da-md py-[10px] text-foreground da-mono">
                    {table.column_count ?? '—'}
                  </td>
                  <td className="px-da-md py-[10px] text-muted-foreground da-body-small">
                    {table.refreshed_at ? formatTime(table.refreshed_at) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <TablePager view={view} />
          </>
        )}
      </div>
    </div>
  );
}
