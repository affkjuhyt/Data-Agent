import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Input } from '../../design-system/Input';
import { Menu } from '../../design-system/Menu';
import { Select } from '../../design-system/Select';
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
const ALL = '_all';

export default function SourcesPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const sources = useV1(listSourceRows, []);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState(ALL);
  const [mode, setMode] = useState(ALL);
  const [space, setSpace] = useState(ALL);

  const items = useMemo(
    () => (sources.data?.items ?? []).filter((s) => s.status !== 'archived'),
    [sources.data],
  );
  const kinds = [...new Set(items.map((s) => s.connector_label))];
  const spaces = [
    ...new Map(items.flatMap((s) => s.data_spaces).map((d) => [d.id, d.name])).entries(),
  ];

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter(
      (s) =>
        (!q || s.name.toLowerCase().includes(q) || s.endpoint.toLowerCase().includes(q)) &&
        (kind === ALL || s.connector_label === kind) &&
        (mode === ALL || s.mode === mode) &&
        (space === ALL || s.data_spaces.some((d) => d.id === space)),
    );
  }, [items, query, kind, mode, space]);

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
        <div className="flex flex-wrap items-center gap-da-md">
          <Input
            className="w-[300px] max-[639px]:w-full"
            icon="search"
            placeholder="Tìm nguồn, bảng…"
            aria-label="Tìm nguồn"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Select
            className="w-[180px] max-[639px]:w-full"
            label="Lọc theo loại"
            value={kind}
            onChange={setKind}
            options={[
              { value: ALL, label: 'Loại: tất cả' },
              ...kinds.map((k) => ({ value: k, label: `Loại: ${k}` })),
            ]}
          />
          <Select
            className="w-[200px] max-[639px]:w-full"
            label="Lọc theo chế độ"
            value={mode}
            onChange={setMode}
            options={[
              { value: ALL, label: 'Chế độ: tất cả' },
              { value: 'federated', label: 'Chế độ: truy vấn tại chỗ' },
              { value: 'ingest', label: 'Chế độ: nạp về' },
            ]}
          />
          <Select
            className="w-[220px] max-[639px]:w-full"
            label="Lọc theo Data Space"
            value={space}
            onChange={setSpace}
            options={[
              { value: ALL, label: 'Data Space: tất cả' },
              ...spaces.map(([id, name]) => ({ value: id, label: `Data Space: ${name}` })),
            ]}
          />
        </div>
        <AsyncRegion state={sources} what="danh sách nguồn" skeletonRows={6}>
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal max-[639px]:px-da-sm">Nguồn</th>
                  <th className="w-[120px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    Loại
                  </th>
                  {/* Narrower screens drop columns from the right; what a dropped column said
                      moves under the source's name, so the name column never collapses. */}
                  <th className="w-[150px] px-da-md py-da-md font-normal max-[1023px]:hidden">
                    Chế độ
                  </th>
                  <th className="w-[190px] px-da-md py-da-md font-normal max-[639px]:w-[132px]">
                    Trạng thái
                  </th>
                  <th className="w-[140px] px-da-md py-da-md font-normal max-[767px]:hidden">
                    Dữ liệu mới nhất
                  </th>
                  <th className="w-[150px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    Data Space
                  </th>
                  <th className="w-[44px] px-da-md py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((source) => (
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
                {visible.length === 0 && (
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
        <p className="text-muted-foreground da-caption">
          Truy vấn tại chỗ: agent chạy câu lệnh chỉ đọc trên nguồn, không sao dữ liệu về. Nạp về: dữ
          liệu được sao vào kho của Data Agent theo lịch hoặc liên tục.
        </p>
      </div>
    </div>
  );
}
