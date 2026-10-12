import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AgentChip } from '../../design-system/AgentChip';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { FilterChips } from '../../design-system/FilterChips';
import { Input } from '../../design-system/Input';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { listRuns } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';
import { matchesFilter, runDuration, runStatus, shortCost, type RunFilter } from './runStatus';

/*
 * "v3.1 · Lượt chạy · danh sách" (2060:1627, UC-13-06): every run of every agent — who started
 * it, what it did, where it stopped, what it cost. Status chips and a search over goal, agent
 * and run code; a table in a radius-lg card with a subtle header (6×12) and 8×12 rows.
 * A row opens "Lượt chạy · Đang làm gì". Exporting the log belongs to Kiểm toán (not built).
 */
export default function RunsPage() {
  const { navigate } = useAppState();
  const runs = useV1(() => listRuns(), []);
  const [filter, setFilter] = useState<RunFilter>('all');
  const [query, setQuery] = useState('');

  const items = runs.data?.items ?? [];
  const count = (f: RunFilter) => items.filter((r) => matchesFilter(r, f)).length;
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items
      .filter((r) => matchesFilter(r, filter))
      .filter(
        (r) => !q || [r.title, r.agent_name, r.code].some((t) => t.toLowerCase().includes(q)),
      );
  }, [items, filter, query]);

  return (
    <div className="flex flex-col gap-da-lg px-da-3xl py-da-xl">
      <PageHeader
        breadcrumb={['Agents', 'Lượt chạy']}
        title="Lượt chạy"
        description="Mọi lượt chạy của mọi agent: ai khởi tạo, đã làm gì, dừng ở đâu, tốn bao nhiêu."
        actions={
          <Tooltip content="Xuất nhật ký nằm ở Kiểm toán, đang được làm.">
            <Button disabled>Xuất nhật ký</Button>
          </Tooltip>
        }
      />
      <div className="flex items-center gap-da-md">
        <FilterChips<RunFilter>
          label="Lọc lượt chạy"
          value={filter}
          onChange={setFilter}
          items={[
            { value: 'all', label: 'Tất cả' },
            { value: 'active', label: `Đang chạy · ${count('active')}` },
            { value: 'waiting', label: `Chờ người · ${count('waiting')}` },
            { value: 'failed', label: `Lỗi · ${count('failed')}` },
            { value: 'done', label: 'Hoàn tất' },
          ]}
        />
        <span className="flex-1" />
        <Input
          className="w-[260px]"
          icon="search"
          placeholder="Tìm theo mục tiêu, agent, #lượt"
          aria-label="Tìm lượt chạy"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <AsyncRegion
        state={runs}
        what="lượt chạy"
        skeletonRows={6}
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState
            icon="play"
            title="Chưa có lượt chạy nào"
            description="Khi một agent chạy theo lịch, sự kiện hoặc khi bạn giao việc, lượt chạy sẽ hiện ở đây."
          />
        }
      >
        {() => (
          <div className="overflow-hidden rounded-lg border border-border bg-card">
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="bg-subtle text-left text-muted-foreground da-label">
                  <th className="w-[76px] px-da-lg py-da-sm font-medium">Lượt</th>
                  <th className="px-da-md py-da-sm font-medium">Mục tiêu</th>
                  <th className="w-[230px] px-da-md py-da-sm font-medium">Agent chính</th>
                  <th className="w-[150px] px-da-md py-da-sm font-medium max-[1279px]:hidden">
                    Khởi tạo bởi
                  </th>
                  <th className="w-[130px] px-da-md py-da-sm font-medium">Trạng thái</th>
                  <th className="w-[80px] px-da-md py-da-sm font-medium">Thời gian</th>
                  <th className="w-[52px] px-da-md py-da-sm font-medium">HITL</th>
                  <th className="w-[76px] px-da-md py-da-sm font-medium">Chi phí</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((run) => {
                  const status = runStatus(run);
                  return (
                    <tr
                      key={run.id}
                      tabIndex={0}
                      onClick={() => navigate('agents', 'RUN', run.id)}
                      onKeyDown={(e) => e.key === 'Enter' && navigate('agents', 'RUN', run.id)}
                      className="cursor-pointer border-t border-border hover:bg-subtle focus-visible:bg-subtle"
                    >
                      <td className="px-da-lg py-da-md text-foreground da-mono">{run.code}</td>
                      <td className="truncate px-da-md py-da-md text-foreground da-body-small">
                        {run.title}
                      </td>
                      <td className="px-da-md py-da-md">
                        <AgentChip name={run.agent_name} status={status.chip} />
                      </td>
                      <td className="truncate px-da-md py-da-md text-foreground da-body-small max-[1279px]:hidden">
                        {run.initiated_label}
                      </td>
                      <td className="px-da-md py-da-md">
                        <StatusDot tone={status.tone}>{status.label}</StatusDot>
                      </td>
                      <td className="px-da-md py-da-md text-foreground da-mono">
                        {runDuration(run)}
                      </td>
                      <td className="px-da-md py-da-md text-foreground da-mono">
                        {run.hitl_count}
                      </td>
                      <td className="px-da-md py-da-md text-foreground da-mono">
                        {shortCost(run.cost)}
                      </td>
                    </tr>
                  );
                })}
                {visible.length === 0 && (
                  <tr>
                    <td
                      colSpan={8}
                      className="px-da-lg py-da-xl text-muted-foreground da-body-small"
                    >
                      Không có lượt chạy nào khớp bộ lọc.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </AsyncRegion>
    </div>
  );
}
