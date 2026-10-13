import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { LinkCard } from '../../design-system/LinkCard';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  createModelCard,
  formatTwo,
  getForecastWorkbench,
  rerunBacktest,
  type ForecastWorkbench,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { DataTable, Stat } from './parts';

/*
 * "P15 · Xưởng dự báo" (2391:14820, UC-15-02, UC-15-03): what the forecasting agent prepared for
 * one job — the target series checked for length, gaps, breaks and outliers, the exogenous
 * variables aligned on when they were recorded (leaky ones excluded, BR-157), and the rolling
 * backtest against seasonal naive. The selected model becomes a draft model card that someone
 * other than its creator approves (BR-158).
 */
type Tab = 'data' | 'variables' | 'backtest' | 'card';

const TABS: { value: Tab; label: string }[] = [
  { value: 'data', label: 'Dữ liệu' },
  { value: 'variables', label: 'Biến và giả định' },
  { value: 'backtest', label: 'Backtest' },
  { value: 'card', label: 'Thẻ mô hình' },
];

export default function ForecastStudioPage() {
  const { routeId } = useAppState();
  const bench = useV1(routeId ? () => getForecastWorkbench(routeId) : null, [routeId]);
  if (!routeId) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="sliders"
          title="Chưa chọn việc dự báo"
          description="Xưởng dự báo mở từ một câu hỏi dự báo, qua “Sửa bộ biến”."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={bench} what="xưởng dự báo">
      {(b) => <StudioBody bench={b} onChanged={bench.reload} />}
    </AsyncRegion>
  );
}

function StudioBody({ bench, onChanged }: { bench: ForecastWorkbench; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('backtest');
  const [busy, setBusy] = useState<'rerun' | 'card' | null>(null);
  const cardLabel = `thẻ mô hình ${bench.next_version_label}`.trim();

  const rerun = async () => {
    if (!bench.forecast_model_id) return;
    setBusy('rerun');
    try {
      await rerunBacktest(bench.forecast_model_id);
      toast({ tone: 'success', message: 'Agent Dự báo đang chạy lại backtest' });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const createCard = async () => {
    if (!bench.version_id) return;
    setBusy('card');
    try {
      const { model_id } = await createModelCard(bench.version_id);
      navigate('analysis', 'FORECAST_MODEL', model_id);
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
      setBusy(null);
    }
  };

  const openModel = () => bench.model_id && navigate('analysis', 'FORECAST_MODEL', bench.model_id);
  const cardButton = bench.model_id ? (
    <Button kind="primary" onClick={openModel}>
      Mở {cardLabel}
    </Button>
  ) : bench.can_create_card.allowed ? (
    <Button kind="primary" loading={busy === 'card'} onClick={createCard}>
      Tạo {cardLabel}
    </Button>
  ) : (
    <Tooltip content={bench.can_create_card.reason ?? 'Chưa tạo được thẻ mô hình.'}>
      <Button kind="primary" disabled>
        Tạo {cardLabel}
      </Button>
    </Tooltip>
  );

  const stats = (
    <div className="flex flex-wrap gap-da-xl">
      {bench.series_stats.map((s) => (
        <Stat key={s.label} stat={s} />
      ))}
    </div>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', bench.metric_name]}
        title={`Xưởng dự báo · ${bench.metric_name}`}
        description={bench.summary_text}
        actions={
          <>
            <Button
              loading={busy === 'rerun'}
              disabled={!bench.forecast_model_id}
              onClick={rerun}
            >
              Chạy lại backtest
            </Button>
            {cardButton}
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <Tabs<Tab> label="Xưởng dự báo" items={TABS} value={tab} onChange={setTab} />
        {tab === 'data' &&
          (bench.series_stats.length ? (
            stats
          ) : (
            <EmptyState
              icon="chart"
              title="Chưa có phiên bản mô hình"
              description="Agent Dự báo chưa huấn luyện xong phiên bản nào cho việc này."
            />
          ))}
        {tab === 'variables' && <VariablesCard bench={bench} />}
        {tab === 'backtest' && (
          <>
            {stats}
            <div className="flex flex-wrap items-start gap-da-xl">
              <VariablesCard bench={bench} className="w-[600px] max-w-full" />
              <BacktestCard bench={bench} />
            </div>
          </>
        )}
        {tab === 'card' &&
          (bench.model_id ? (
            <LinkCard
              icon="file"
              title={`Thẻ mô hình ${bench.next_version_label}`.trim()}
              corner="chevron"
              onOpen={openModel}
            >
              <p className="text-muted-foreground da-caption">
                Nháp chờ người phụ trách chỉ số duyệt; người tạo không tự duyệt (BR-158).
              </p>
            </LinkCard>
          ) : (
            <EmptyState
              icon="file"
              title={`Chưa có ${cardLabel}`}
              description={
                bench.can_create_card.allowed
                  ? 'Thẻ mô hình ghi mục tiêu, dữ liệu, biến, thuật toán và kết quả backtest để người phụ trách chỉ số duyệt.'
                  : (bench.can_create_card.reason ?? 'Chưa tạo được thẻ mô hình.')
              }
              action={bench.can_create_card.allowed ? cardButton : undefined}
            />
          ))}
      </div>
    </div>
  );
}

function VariablesCard({ bench, className }: { bench: ForecastWorkbench; className?: string }) {
  const used = bench.variables.filter((v) => v.status === 'used').length;
  const excluded = bench.variables.length - used;
  return (
    <Card
      size="sm"
      title={`Biến ngoại sinh · ${used} dùng · ${excluded} loại`}
      className={className}
    >
      <DataTable
        label="Biến ngoại sinh"
        rows={bench.variables}
        rowKey={(v) => v.name}
        columns={[
          {
            head: 'Biến',
            width: 'w-[190px]',
            cell: (v) => (
              <span className="flex flex-col gap-[2px]">
                <span className="text-foreground da-body-strong">{v.name}</span>
                <span className="text-muted-foreground da-caption">{v.source}</span>
              </span>
            ),
          },
          { head: 'Trễ', cell: (v) => v.lag_text },
          { head: 'Tương quan', cell: (v) => v.correlation_text },
          { head: 'Giá trị tương lai', cell: (v) => v.future_value_text },
          {
            head: 'Trạng thái',
            cell: (v) => (
              <StatusDot tone={v.status === 'used' ? 'success' : 'error'}>
                {v.status_text}
              </StatusDot>
            ),
          },
        ]}
      />
      <p className="text-muted-foreground da-caption">{bench.variables_note}</p>
    </Card>
  );
}

function BacktestCard({ bench }: { bench: ForecastWorkbench }) {
  return (
    <Card size="sm" title="Backtest · MASE so seasonal naive" className="min-w-[420px] flex-1">
      <DataTable
        label="Backtest"
        rows={bench.backtest}
        rowKey={(r) => r.model}
        rowClassName={(r) => (r.role === 'selected' ? 'bg-accent' : undefined)}
        columns={[
          {
            head: 'Mô hình',
            cell: (r) => (
              <span className={r.role === 'selected' ? 'font-semibold' : undefined}>{r.model}</span>
            ),
          },
          {
            head: 'MASE',
            cell: (r) => (
              <span className={r.role === 'selected' ? 'font-semibold' : undefined}>
                {r.mase === null ? '—' : formatTwo(r.mase)}
              </span>
            ),
          },
          { head: 'sMAPE', cell: (r) => r.smape_text },
          { head: 'Phủ 80%', cell: (r) => r.cover80_text },
          { head: 'Phủ 95%', cell: (r) => r.cover95_text },
          {
            head: 'Vai trò',
            hideHead: true,
            cell: (r) =>
              r.role === 'selected' ? (
                <Badge tone="accent">Chọn</Badge>
              ) : r.role === 'baseline' ? (
                <span className="text-muted-foreground">chuẩn</span>
              ) : null,
          },
        ]}
      />
      <p className="text-muted-foreground da-caption">{bench.backtest_note}</p>
    </Card>
  );
}
