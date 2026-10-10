import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatDate, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getAcquireSummary,
  listCrawlSources,
  runCrawlSource,
  type CrawlHealth,
  type CrawlSource,
  type ReviewGate,
} from '../../lib/v1/crawl';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P14 · Nguồn thu thập · Danh sách" (2389:14334, UC-14-01, UC-14-06): every external source the
 * Agent Thu thập collects from. Four figures (sources collecting, random-sample accuracy, records
 * waiting for review, the latest consolidated data), then one row per source with its format,
 * schedule, health, last run, accuracy, queue and review gate. A row is selected to run it now;
 * its name opens the source.
 */
type Tone = 'success' | 'warning' | 'error' | 'info' | 'neutral';

const HEALTH: Record<CrawlHealth, Tone> = {
  on_schedule: 'success',
  drift_quarantined: 'warning',
  blocked: 'error',
  pending_schema: 'info',
  paused: 'neutral',
};

const GATE: Record<ReviewGate, string> = {
  auto: 'Tự động',
  exceptions: 'Duyệt ngoại lệ',
  all: 'Duyệt hết',
};

const pct = (n: number) => (n === 100 ? '100%' : `${n.toFixed(1).replace('.', ',')}%`);

export default function CrawlSourcesPage() {
  const { navigate } = useAppState();
  const sources = useV1(listCrawlSources, []);
  const summary = useV1(getAcquireSummary, []);
  const toast = useToast();
  const [selected, setSelected] = useState<string>();
  const [running, setRunning] = useState(false);

  const items = sources.data?.items ?? [];
  const chosen = items.find((s) => s.id === selected);
  const count = (h: CrawlHealth) => items.filter((s) => s.health === h).length;

  const runNow = async () => {
    if (!chosen) return;
    setRunning(true);
    try {
      await runCrawlSource(chosen.id);
      toast({ tone: 'info', message: `Đang thu ${chosen.name}` });
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy được: ${(e as Error).message}` });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Thu thập ngoài']}
        title="Nguồn thu thập"
        description={
          sources.data
            ? [
                `${items.length} nguồn`,
                `${count('on_schedule')} đúng lịch`,
                `${count('drift_quarantined')} đổi cấu trúc`,
                `${count('blocked')} bị chặn`,
                `${items.reduce((n, s) => n + s.pending_review, 0)} bản ghi chờ duyệt`,
              ].join(' · ')
            : undefined
        }
        actions={
          <>
            {chosen ? (
              <Button loading={running} onClick={runNow}>
                Chạy ngay
              </Button>
            ) : (
              <Tooltip content="Chọn một nguồn trong bảng để chạy ngay.">
                <Button disabled>Chạy ngay</Button>
              </Tooltip>
            )}
            <Button kind="primary" onClick={() => navigate('data', 'CRAWL_NEW')}>
              Thêm nguồn thu thập
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {summary.data && (
          <div className="flex gap-da-xl max-[1023px]:flex-wrap">
            <Stat
              label="Nguồn đang thu"
              value={`${summary.data.active} / ${summary.data.total}`}
              note={`${summary.data.paused} tạm ngưng · ${summary.data.blocked} bị chặn`}
              tone="warning"
              foot={summary.data.last_agent_run_text}
            />
            <Stat
              label="Độ chính xác mẫu ngẫu nhiên"
              value={pct(summary.data.sample_accuracy_pct)}
              note={summary.data.accuracy_delta_text}
              tone="success"
              foot={summary.data.sample_text}
            />
            <Stat
              label="Bản ghi chờ duyệt"
              value={String(summary.data.pending_records)}
              note={`${summary.data.blocking_records} mục chặn phát hành`}
              tone="warning"
              foot="Hàng duyệt dữ liệu thu thập"
            />
            {summary.data.latest_dataset && (
              <Stat
                label="Dữ liệu hợp nhất mới nhất"
                value={`${formatDate(summary.data.latest_dataset.published_at).slice(0, 5)} ${formatTime(summary.data.latest_dataset.published_at)}`}
                note={summary.data.latest_dataset.title}
                foot={summary.data.latest_dataset.detail}
              />
            )}
          </div>
        )}
        <AsyncRegion state={sources} what="nguồn thu thập" skeletonRows={6}>
          {() => (
            <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border">
              <thead>
                <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                  <th className="w-[260px] px-da-lg py-da-md font-normal">Nguồn</th>
                  <th className="px-da-lg py-da-md font-normal">Định dạng</th>
                  <th className="px-da-lg py-da-md font-normal">Lịch</th>
                  <th className="w-[190px] px-da-lg py-da-md font-normal">Trạng thái</th>
                  <th className="px-da-lg py-da-md font-normal">Lần thu cuối</th>
                  <th className="w-[80px] px-da-lg py-da-md font-normal">Chính xác</th>
                  <th className="w-[56px] px-da-lg py-da-md font-normal">Chờ</th>
                  <th className="w-[120px] px-da-lg py-da-md font-normal">Cổng duyệt</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <SourceRow
                    key={s.id}
                    source={s}
                    selected={s.id === selected}
                    onSelect={() => setSelected(s.id === selected ? undefined : s.id)}
                    onOpen={() => navigate('data', 'CRAWL_SOURCE', s.id)}
                    onOpenDrift={(driftId) => navigate('data', 'CRAWL_DRIFT', driftId)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          Agent Thu thập chỉ tải trong các miền đã khai báo, tuân thủ robots.txt, tối đa 1 yêu cầu
          mỗi giây mỗi miền (BR-149). Bản gốc mọi trang được lưu nguyên kèm SHA-256 (BR-150). Nguồn
          chỉ chạy khi đã duyệt pháp lý; điều khoản được băm và kiểm lại mỗi lượt.
        </p>
      </div>
    </div>
  );
}

function SourceRow({
  source,
  selected,
  onSelect,
  onOpen,
  onOpenDrift,
}: {
  source: CrawlSource;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onOpenDrift: (driftId: string) => void;
}) {
  return (
    <tr
      aria-selected={selected}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        // Rows are selected with Space or Enter as well as the mouse.
        if (e.target === e.currentTarget && (e.key === ' ' || e.key === 'Enter')) {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        'cursor-pointer border-b border-border last:border-b-0 da-body-small',
        selected ? 'bg-accent' : 'hover:bg-subtle',
      )}
    >
      <td className="px-da-lg py-[10px]">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
          className="text-left text-foreground da-body-strong hover:underline"
        >
          {source.name}
        </button>
        <p className="text-muted-foreground da-mono">
          {source.kind} · {source.scope_text}
        </p>
      </td>
      <td className="px-da-lg py-[10px] text-foreground">{source.format_label}</td>
      <td className="px-da-lg py-[10px] text-foreground">{source.schedule_text}</td>
      <td className="px-da-lg py-[10px]">
        {source.drift_id ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenDrift(source.drift_id as string);
            }}
            className="hover:underline"
          >
            <StatusDot tone={HEALTH[source.health]}>{source.health_text}</StatusDot>
          </button>
        ) : (
          <StatusDot tone={HEALTH[source.health]}>{source.health_text}</StatusDot>
        )}
      </td>
      <td className="px-da-lg py-[10px] text-foreground">{source.last_run_text ?? '—'}</td>
      <td className="px-da-lg py-[10px] text-foreground">
        {source.sample_accuracy_pct === null ? '—' : pct(source.sample_accuracy_pct)}
      </td>
      <td className="px-da-lg py-[10px] text-foreground">{source.pending_review}</td>
      <td className="px-da-lg py-[10px] text-foreground">{GATE[source.review_gate]}</td>
    </tr>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
  foot,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'warning' | 'success';
  foot: string;
}) {
  return (
    <div className="flex min-w-[200px] flex-1 flex-col gap-[6px] rounded-md border border-border bg-card px-da-xl py-[14px]">
      <p className="text-muted-foreground da-caption">{label}</p>
      <p className="text-foreground da-metric">{value}</p>
      <p
        className={cn(
          'da-caption',
          tone === 'warning'
            ? 'text-warning'
            : tone === 'success'
              ? 'text-success'
              : 'text-foreground',
        )}
      >
        {note}
      </p>
      <p className="text-muted-foreground da-caption">{foot}</p>
    </div>
  );
}
