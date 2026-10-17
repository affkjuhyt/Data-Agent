import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Tooltip } from '../../design-system/Tooltip';
import { csvCell } from '../../lib/csv';
import { cn } from '../../lib/utils';
import { comparePeriodVersions, type PeriodComparison } from '../../lib/v1/periods';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P10 · So sánh hai bản" (2304:19616, UC-10-05): two versions of a period side by side, figure
 * by figure, each gap with the reason that traces it back to the retro change (or run) that made
 * it. "Đổi bản" swaps the two; "Xuất" downloads the table as CSV. Comparing two points in time
 * of the current data and adding the table to a session wait for their screens.
 */
const DEFAULT = { left: 'pv-2026-08-v1', right: 'pv-2026-08-v2' };

function csvOf(result: PeriodComparison): string {
  const head = ['Chỉ số', result.left.label, result.right.label, 'Chênh', 'Vì sao'];
  const rows = result.rows.map((r) => [r.metric, r.left, r.right, r.delta, r.reason ?? '']);
  return [head, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
}

function download(name: string, text: string) {
  // A BOM so Excel opens the Vietnamese text as UTF-8.
  const url = URL.createObjectURL(new Blob(['\ufeff', text], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export default function CompareVersionsPage() {
  const { navigate } = useAppState();
  const [pair, setPair] = useState(DEFAULT);
  const result = useV1(() => comparePeriodVersions(pair.left, pair.right), [pair.left, pair.right]);

  return (
    <AsyncRegion state={result} what="so sánh">
      {(r) => (
        <div className="flex min-h-full flex-col">
          <PageHeader
            bordered
            breadcrumb={['Dữ liệu', 'Kỳ chốt', 'So sánh']}
            title={`So sánh ${shortLabel(r.left.label)} với ${shortLabel(r.right.label)}`}
            description={`${versionTag(r.left.label)}: ${r.left.note} · ${versionTag(r.right.label)}: ${r.right.note}`}
            actions={
              <>
                <Button
                  kind="ghost"
                  onClick={() => setPair({ left: pair.right, right: pair.left })}
                >
                  Đổi bản
                </Button>
                <Button onClick={() => download('so-sanh-ky.csv', csvOf(r))}>Xuất</Button>
                <Tooltip content="Thêm bảng vào phiên cần màn Phiên làm việc mới.">
                  <Button kind="primary" disabled>
                    Thêm vào phiên
                  </Button>
                </Tooltip>
              </>
            }
          />
          <div className="flex flex-wrap items-center gap-[10px] border-b border-border px-da-3xl py-[10px]">
            {(['left', 'right'] as const).map((side, i) => (
              <span key={side} className="flex items-center gap-[6px]">
                <span className="text-muted-foreground da-caption">Bản {i + 1}</span>
                <Select
                  label={`Bản ${i + 1}`}
                  className="w-[240px]"
                  value={pair[side]}
                  options={r.versions.map((v) => ({ value: v.id, label: v.label }))}
                  onChange={(id) => setPair({ ...pair, [side]: id })}
                />
              </span>
            ))}
            <Tooltip content="So theo mốc giờ của dữ liệu hiện tại đang được làm.">
              <span className="text-muted-foreground da-body-small">
                hoặc so hai mốc giờ của dữ liệu hiện tại
              </span>
            </Tooltip>
          </div>
          <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
            <ComparisonTable
              rows={r.rows}
              onRetro={(id) => navigate('data', 'RETRO', id)}
            />
            <p className="text-muted-foreground da-caption">
              Mỗi chênh lệch nối về thay đổi hồi tố hoặc lần chạy đã tạo ra nó.
            </p>
          </div>
        </div>
      )}
    </AsyncRegion>
  );
}

function ComparisonTable({
  rows,
  onRetro,
}: {
  rows: PeriodComparison['rows'];
  onRetro: (id: string) => void;
}) {
  const view = useTableView(rows, {
    search: (row) => `${row.metric} ${row.left} ${row.right} ${row.delta} ${row.reason ?? ''}`,
    filters: [
      { id: 'changed', label: 'Chênh', value: (row) => (row.changed ? 'Có chênh' : 'Không đổi') },
    ],
    sorts: [
      { id: 'metric', value: (row) => row.metric },
      { id: 'left', value: (row) => row.left },
      { id: 'right', value: (row) => row.right },
      { id: 'delta', value: (row) => row.delta },
      { id: 'reason', value: (row) => row.reason },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm chỉ số…" searchLabel="Tìm chỉ số" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="metric" className="px-da-lg py-da-md font-normal">
              Chỉ số
            </SortableTh>
            <SortableTh view={view} column="left" className="w-[130px] px-da-lg py-da-md font-normal">
              Bản 1
            </SortableTh>
            <SortableTh view={view} column="right" className="w-[130px] px-da-lg py-da-md font-normal">
              Bản 2
            </SortableTh>
            <SortableTh view={view} column="delta" className="w-[120px] px-da-lg py-da-md font-normal">
              Chênh
            </SortableTh>
            <SortableTh
              view={view}
              column="reason"
              className="w-[360px] px-da-lg py-da-md font-normal max-[1279px]:w-[240px]"
            >
              Vì sao
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.metric} className="border-b border-border">
              <td className="px-da-lg py-da-md text-foreground da-body-strong">{row.metric}</td>
              <td className="px-da-lg py-da-md text-foreground da-mono">{row.left}</td>
              <td className="px-da-lg py-da-md text-foreground da-mono">{row.right}</td>
              <td
                className={cn(
                  'px-da-lg py-da-md da-mono',
                  row.changed ? 'text-warning' : 'text-muted-foreground',
                )}
              >
                {row.delta}
              </td>
              <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                {row.retro_change_id &&
                // Only the first row of a retro change links to it; the rest say "như trên".
                rows.find((x) => x.retro_change_id === row.retro_change_id) === row ? (
                  <button
                    type="button"
                    onClick={() => onRetro(row.retro_change_id ?? '')}
                    className="text-left text-primary hover:underline"
                  >
                    {row.reason}
                  </button>
                ) : (
                  (row.reason ?? '—')
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}

/** "T8/2026 · v1 · đã công bố" → "T8 · v1" for the title. */
function shortLabel(label: string): string {
  const [period, version] = label.split(' · ');
  return `${period.split('/')[0]} · ${version}`;
}

/** "T8/2026 · v2 · điều chỉnh" → "v2" for the line under the title. */
function versionTag(label: string): string {
  return label.split(' · ')[1] ?? label;
}
