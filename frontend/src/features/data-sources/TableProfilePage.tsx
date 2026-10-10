import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber, formatPercent, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getTableProfile,
  reprofileTable,
  type ColumnProfile,
  type TableProfile,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P04 · Nguồn · Hồ sơ bảng" (2280:16974, UC-04-02): the profile of one table — per column its
 * type, share empty, distinct values, a small histogram and a note — and, on the right (340px),
 * the Data Prep agent's remarks. Only "Hồ sơ dữ liệu" has an API; the other tabs say so.
 * "Xem ánh xạ" and "Đặt quy tắc chất lượng" of the agent panel have no screen yet and are not drawn.
 */
type Tab = 'profile' | 'sample' | 'schema' | 'lineage' | 'access';

export default function TableProfilePage() {
  const { routeId } = useAppState();
  const profile = useV1(routeId ? () => getTableProfile(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={profile} what="hồ sơ bảng" skeletonRows={8}>
      {(data) => <ProfileView profile={data} />}
    </AsyncRegion>
  );
}

function ProfileView({ profile }: { profile: TableProfile }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('profile');
  const [queued, setQueued] = useState(false);

  const reprofile = async () => {
    try {
      await reprofileTable(profile.id);
      setQueued(true);
      toast({ tone: 'success', message: `Đã xếp lịch lập hồ sơ lại ${profile.name}.` });
    } catch (e) {
      toast({ tone: 'error', message: `Không lập hồ sơ lại được. ${(e as Error).message}` });
    }
  };

  const facts = [
    `${formatNumber(profile.row_count)} dòng`,
    `${profile.column_count} cột`,
    profile.refreshed_at ? `cập nhật ${formatTime(profile.refreshed_at)}` : null,
    `hồ sơ lập lúc ${profile.profiled_text}`,
  ].filter(Boolean);

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Nguồn', profile.source.name, profile.name]}
        title={profile.name}
        description={facts.join(' · ')}
        actions={
          <>
            <Button kind="ghost" onClick={() => void reprofile()} disabled={queued}>
              Lập hồ sơ lại
            </Button>
            <Button onClick={() => navigate('analysis', 'ASK')}>Hỏi về bảng này</Button>
            <Tooltip content="Khám phá kéo thả đang được làm.">
              <Button kind="primary" disabled>
                Dùng trong Khám phá
              </Button>
            </Tooltip>
          </>
        }
      />
      <Tabs<Tab>
        className="px-da-3xl"
        label="Mục của bảng"
        value={tab}
        onChange={setTab}
        items={[
          { value: 'profile', label: 'Hồ sơ dữ liệu' },
          { value: 'sample', label: 'Mẫu dữ liệu' },
          { value: 'schema', label: 'Cấu trúc & lịch sử' },
          { value: 'lineage', label: 'Dòng dữ liệu' },
          { value: 'access', label: 'Quyền' },
        ]}
      />
      {tab === 'profile' ? (
        <div className="flex flex-1 max-[1023px]:flex-col">
          <div className="min-w-0 flex-1 pl-da-3xl pr-da-2xl pt-da-md">
            {profile.columns.length === 0 ? (
              <EmptyState
                className="mt-da-lg"
                icon="table"
                title="Bảng chưa có hồ sơ"
                description="Hồ sơ dữ liệu được lập theo lịch trên mẫu đã che dữ liệu cá nhân. Bấm Lập hồ sơ lại để lập ngay."
              />
            ) : (
              <ColumnsTable columns={profile.columns} />
            )}
          </div>
          {profile.review && (
            <aside
              aria-label={`${profile.review.agent_name} nhận xét`}
              className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t max-[1023px]:pb-da-xl"
            >
              <h2 className="text-foreground da-heading-3">{profile.review.agent_name} nhận xét</h2>
              {profile.review.remarks.map((remark) => (
                <p key={remark} className="text-foreground da-body-small">
                  · {remark}
                </p>
              ))}
              <p className="text-muted-foreground da-caption">{profile.review.footnote}</p>
            </aside>
          )}
        </div>
      ) : (
        <p className="px-da-3xl py-da-xl text-muted-foreground da-body-small">
          Mục này đang được làm.
        </p>
      )}
    </div>
  );
}

function distinctText(column: ColumnProfile): string {
  if (column.distinct === 'unique') return '100%';
  return column.distinct === null ? '—' : formatNumber(column.distinct);
}

function ColumnsTable({ columns }: { columns: ColumnProfile[] }) {
  return (
    <table className="w-full table-fixed border-collapse">
      <thead>
        <tr className="border-b border-border text-left text-muted-foreground da-caption">
          <th className="w-[170px] px-da-lg py-da-md font-normal">Cột</th>
          <th className="w-[110px] px-da-md py-da-md font-normal">Kiểu</th>
          <th className="w-[70px] px-da-md py-da-md font-normal">Trống</th>
          <th className="w-[90px] px-da-md py-da-md font-normal">Khác nhau</th>
          <th className="w-[180px] px-da-md py-da-md font-normal max-[1279px]:hidden">Phân bố</th>
          <th className="px-da-md py-da-md font-normal">Ghi chú</th>
        </tr>
      </thead>
      <tbody>
        {columns.map((column) => (
          <tr key={column.name} className="border-b border-border">
            <td className="truncate px-da-lg py-[9px] text-foreground da-mono">{column.name}</td>
            <td className="truncate px-da-md py-[9px] text-muted-foreground da-mono">
              {column.data_type}
            </td>
            <td
              className={cn(
                'px-da-md py-[9px] da-mono',
                column.warn ? 'text-warning' : 'text-foreground',
              )}
            >
              {column.null_ratio === null ? '—' : formatPercent(column.null_ratio)}
            </td>
            <td className="px-da-md py-[9px] text-foreground da-mono">{distinctText(column)}</td>
            <td className="px-da-md py-[9px] max-[1279px]:hidden">
              <Histogram column={column} />
            </td>
            <td
              className={cn(
                'px-da-md py-[9px] da-body-small',
                column.warn ? 'text-warning' : 'text-muted-foreground',
              )}
            >
              {column.note ?? ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Bars of chart-6, bottom-aligned in an 18px strip; five buckets draw wider than ten. */
function Histogram({ column }: { column: ColumnProfile }) {
  if (!column.histogram) {
    return <span className="text-muted-foreground da-caption">{column.histogram_note ?? '—'}</span>;
  }
  const wide = column.histogram.length <= 5;
  return (
    <span
      className="flex h-[18px] items-end gap-[2px]"
      role="img"
      aria-label={`Phân bố của ${column.name}`}
    >
      {column.histogram.map((h, i) => (
        <span
          key={i}
          className={cn('bg-chart-6', wide ? 'w-6' : 'w-[14px]')}
          style={{ height: Math.max(1, Math.round(h * 18)) }}
        />
      ))}
    </span>
  );
}
