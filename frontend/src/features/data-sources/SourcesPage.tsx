import { useMemo } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Menu } from '../../design-system/Menu';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { listSourceRows, testSource, type SourceRow } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { ConnectorPicker } from './ConnectorPicker';
import {
  dataSpacesText,
  freshnessText,
  modeLabel,
  sourceTone,
  sourcesSummary,
} from './sourceFormat';

/*
 * "P04 · Nguồn · Danh sách" (2279:16522, UC-04-01, UC-04-09): every connected source with its
 * mode, health and freshness. Search over name and endpoint, and three filters (type, mode,
 * Data Space). A row opens the source; "…" checks the connection again. With no source yet
 * the page is "Kết nối dữ liệu · Chưa có nguồn" (2165:17285).
 */
export default function SourcesPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const sources = useV1(listSourceRows, []);

  const items = useMemo(
    () => (sources.data?.items ?? []).filter((s) => s.status !== 'archived'),
    [sources.data],
  );
  const view = useTableView(items, {
    search: (s) => `${s.name} ${s.endpoint} ${s.connector_label}`,
    filters: [
      { id: 'kind', label: 'Loại', value: (s) => s.connector_label },
      {
        id: 'mode',
        label: 'Chế độ',
        control: 'Lọc theo chế độ',
        value: (s) => s.mode,
        text: (mode) => (mode === 'federated' ? 'truy vấn tại chỗ' : 'nạp về'),
      },
      {
        id: 'space',
        label: 'Data Space',
        value: (s) => s.data_spaces.map((d) => d.name).join(', '),
      },
    ],
    sorts: [
      { id: 'name', value: (s) => s.name },
      { id: 'kind', value: (s) => s.connector_label },
      { id: 'mode', value: (s) => s.mode },
      { id: 'status', value: (s) => s.status_label },
      { id: 'freshness', value: (s) => freshnessText(s) },
      { id: 'spaces', value: (s) => dataSpacesText(s) },
    ],
  });

  const check = async (source: SourceRow) => {
    try {
      const result = await testSource(source.id);
      const failed = result.checks.filter((c) => !c.ok);
      toast(
        failed.length === 0
          ? {
              tone: 'success',
              message: `${source.name}: ${result.checks.length}/${result.checks.length} kiểm tra đạt.`,
            }
          : { tone: 'error', message: `${source.name}: ${failed[0].title} — không đạt.` },
      );
      sources.reload();
    } catch (e) {
      toast({
        tone: 'error',
        message: `Không kiểm tra được ${source.name}. ${(e as Error).message}`,
      });
    }
  };

  if (sources.data && items.length === 0) return <ConnectorPicker />;

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Nguồn']}
        title="Nguồn dữ liệu"
        description={
          sources.data ? `${sourcesSummary(items)} · mọi kết nối dùng tài khoản chỉ đọc` : undefined
        }
        actions={
          <>
            <Button onClick={() => navigate('data', 'UPLOAD')}>Tải tệp</Button>
            <Button kind="primary" onClick={() => navigate('data', 'CONNECT')}>
              Kết nối nguồn
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl max-[767px]:px-da-lg">
        <TableToolbar view={view} placeholder="Tìm nguồn, bảng…" searchLabel="Tìm nguồn" />
        <AsyncRegion state={sources} what="danh sách nguồn" skeletonRows={6}>
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal max-[639px]:px-da-sm">
                    Nguồn
                  </SortableTh>
                  <SortableTh view={view} column="kind" className="w-[120px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    Loại
                  </SortableTh>
                  {/* Narrower screens drop columns from the right; what a dropped column said
                      moves under the source's name, so the name column never collapses. */}
                  <SortableTh view={view} column="mode" className="w-[150px] px-da-md py-da-md font-normal max-[1023px]:hidden">
                    Chế độ
                  </SortableTh>
                  <SortableTh view={view} column="status" className="w-[190px] px-da-md py-da-md font-normal max-[639px]:w-[132px]">
                    Trạng thái
                  </SortableTh>
                  <SortableTh view={view} column="freshness" className="w-[140px] px-da-md py-da-md font-normal max-[767px]:hidden">
                    Dữ liệu mới nhất
                  </SortableTh>
                  <SortableTh view={view} column="spaces" className="w-[150px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    Data Space
                  </SortableTh>
                  <SortableTh view={view} className="w-[44px] px-da-md py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((source) => (
                  <tr
                    key={source.id}
                    tabIndex={0}
                    onClick={() => navigate('data', 'SOURCE', source.id)}
                    onKeyDown={(e) =>
                      e.key === 'Enter' &&
                      e.target === e.currentTarget &&
                      navigate('data', 'SOURCE', source.id)
                    }
                    className="cursor-pointer border-b border-border hover:bg-subtle focus-visible:bg-subtle"
                  >
                    <td className="px-da-lg py-[10px] max-[639px]:px-da-sm">
                      <div className="flex min-w-0 flex-col gap-[2px]">
                        <span className="truncate text-foreground da-body-strong">
                          {source.name}
                        </span>
                        <span className="truncate text-muted-foreground da-mono">
                          {source.connector} · {source.endpoint}
                        </span>
                        <span className="hidden truncate text-muted-foreground da-caption max-[1023px]:block">
                          {modeLabel(source)}
                          <span className="hidden max-[767px]:inline"> · {freshnessText(source)}</span>
                        </span>
                      </div>
                    </td>
                    <td className="px-da-md py-[10px] text-foreground da-body-small max-[1279px]:hidden">
                      {source.connector_label}
                    </td>
                    <td className="px-da-md py-[10px] text-foreground da-body-small max-[1023px]:hidden">
                      {modeLabel(source)}
                    </td>
                    <td className="px-da-md py-[10px]">
                      <StatusDot
                        tone={sourceTone(source)}
                        className="max-[639px]:items-start max-[639px]:whitespace-normal"
                      >
                        {source.status_label}
                      </StatusDot>
                    </td>
                    <td className="px-da-md py-[10px] text-muted-foreground da-body-small max-[767px]:hidden">
                      {freshnessText(source)}
                    </td>
                    <td className="truncate px-da-md py-[10px] text-muted-foreground da-body-small max-[1279px]:hidden">
                      {dataSpacesText(source)}
                    </td>
                    <td
                      className="px-da-md py-[10px]"
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                    >
                      <Menu
                        label={`Thao tác với ${source.name}`}
                        items={[
                          {
                            label: 'Mở nguồn',
                            onSelect: () => navigate('data', 'SOURCE', source.id),
                          },
                          {
                            label: 'Kiểm tra kết nối',
                            icon: 'refresh',
                            onSelect: () => void check(source),
                          },
                          ...(source.drift_id
                            ? [
                                {
                                  label: 'Duyệt thay đổi cấu trúc',
                                  icon: 'layers' as const,
                                  onSelect: () => navigate('data', 'DRIFT', source.drift_id!),
                                },
                              ]
                            : []),
                        ]}
                      />
                    </td>
                  </tr>
                ))}
                {view.total === 0 && (
                  <tr>
                    <td
                      colSpan={7}
                      className="px-da-lg py-da-xl text-muted-foreground da-body-small"
                    >
                      Không có nguồn nào khớp bộ lọc.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <TablePager view={view} />
        <p className="text-muted-foreground da-caption">
          Truy vấn tại chỗ: agent chạy câu lệnh chỉ đọc trên nguồn, không sao dữ liệu về. Nạp về: dữ
          liệu được sao vào kho của Data Agent theo lịch hoặc liên tục.
        </p>
      </div>
    </div>
  );
}
