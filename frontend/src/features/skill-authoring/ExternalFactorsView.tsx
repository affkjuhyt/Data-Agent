import { useState, type ReactNode } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { StatusDot } from '../../design-system/StatusDot';
import { cn } from '../../lib/utils';
import type {
  ExternalFactor,
  FactorVerdict,
  SkillRun,
  SkillRunForm,
} from '../../lib/v1/skillAuthoring';
import { FormDialog } from '../sessions/FormDialog';

/*
 * "P03 · Tìm yếu tố bên ngoài" (2293:18368, UC-03-02): the approved external series compared
 * with a metric — lag, correlation, a Granger test, and a verdict — with the reminder that moving
 * together is not causing. Picking a series shows it over the metric on the right; both are
 * indexed to one 0…1 scale by the server, so there is one axis. When nothing is significant the
 * screen says so instead of forcing a result.
 */
const VERDICT: Record<FactorVerdict, { label: string; tone: 'success' | 'idle' }> = {
  worth: { label: 'Đáng xem', tone: 'success' },
  weak: { label: 'Yếu', tone: 'idle' },
  random: { label: 'Ngẫu nhiên', tone: 'idle' },
};

export function ExternalFactorsView({
  form,
  run,
  loading,
  error,
  metric,
  busy,
  onChangeMetric,
  addButton,
}: {
  form: SkillRunForm;
  run: SkillRun | null;
  loading: boolean;
  error?: string;
  metric: string | undefined;
  busy: boolean;
  onChangeMetric: (metric: string) => void;
  addButton: ReactNode;
}) {
  const [picking, setPicking] = useState(false);
  const [choice, setChoice] = useState<string | null>(metric ?? null);
  const [selected, setSelected] = useState<string>();
  const search = run?.external;
  const metricParam = form.params.find((p) => p.key === 'metric');
  const factor =
    search?.factors.find((f) => f.id === selected) ?? search?.factors.find((f) => f.overlay);

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', form.name]}
        title={search?.title ?? form.name}
        description={search?.scope_text}
        actions={
          <>
            <Button kind="ghost" loading={busy} disabled={busy} onClick={() => setPicking(true)}>
              Đổi chỉ số
            </Button>
            {addButton}
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <section className="flex min-w-0 flex-1 flex-col gap-da-lg pb-da-2xl pl-da-3xl pr-da-2xl pt-da-xl">
          {error && <Notice tone="error">{error}</Notice>}
          {loading && <SkeletonRows count={4} label="Đang tải kết quả…" />}
          {!loading && !search && !error && (
            <p className="text-muted-foreground da-body-small">Chưa có lần chạy nào.</p>
          )}
          {search && (
            <>
              <p className="rounded-md bg-background px-[14px] py-[10px] text-foreground da-body-small">
                Đi cùng nhau không có nghĩa là gây ra nhau. Kết quả dưới đây là giả thuyết để kiểm
                tra thêm, không phải kết luận.
              </p>
              {search.factors.length === 0 ? (
                <EmptyState
                  icon="search"
                  title="Không tìm thấy yếu tố bên ngoài"
                  description="Không chuỗi nào đã duyệt có liên hệ có ý nghĩa với chỉ số này. Agent không ép ra kết luận."
                />
              ) : (
                <FactorTable factors={search.factors} current={factor?.id} onSelect={setSelected} />
              )}
              <p className="text-muted-foreground da-caption">{search.method_note}</p>
            </>
          )}
        </section>
        <aside className="flex w-[360px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t">
          {factor?.overlay ? (
            <Overlay overlay={factor.overlay} />
          ) : (
            factor && (
              <p className="text-muted-foreground da-body-small">
                “{factor.name}” không có ý nghĩa thống kê nên không vẽ so sánh.
              </p>
            )
          )}
          <p className="text-muted-foreground da-caption">
            Chỉ dùng chuỗi bên ngoài đã được duyệt đưa vào hệ thống
          </p>
        </aside>
      </div>
      {metricParam && (
        <FormDialog
          open={picking}
          title="Đổi chỉ số"
          onClose={() => setPicking(false)}
          footer={
            <>
              <span className="flex-1" />
              <Button kind="ghost" onClick={() => setPicking(false)}>
                Huỷ
              </Button>
              <Button
                kind="primary"
                disabled={!choice}
                onClick={() => {
                  setPicking(false);
                  setSelected(undefined);
                  if (choice) onChangeMetric(choice);
                }}
              >
                Tìm lại
              </Button>
            </>
          }
        >
          <Select
            label="Chỉ số"
            options={metricParam.options}
            value={choice}
            onChange={setChoice}
          />
        </FormDialog>
      )}
    </div>
  );
}

function FactorTable({
  factors,
  current,
  onSelect,
}: {
  factors: ExternalFactor[];
  current: string | undefined;
  onSelect: (id: string) => void;
}) {
  return (
    <table className="w-full table-fixed border-collapse">
      <thead>
        <tr className="border-b border-border text-left text-muted-foreground da-caption">
          <th className="px-da-lg py-da-md font-normal">Chuỗi</th>
          <th className="w-[150px] px-da-lg py-da-md font-normal">Nguồn</th>
          <th className="w-[80px] px-da-lg py-da-md font-normal">Độ trễ</th>
          <th className="w-[90px] px-da-lg py-da-md font-normal">Tương quan</th>
          <th className="w-[110px] px-da-lg py-da-md font-normal">Kiểm định</th>
          <th className="w-[130px] px-da-lg py-da-md font-normal">Kết luận</th>
        </tr>
      </thead>
      <tbody>
        {factors.map((f) => {
          const v = VERDICT[f.verdict];
          return (
            <tr
              key={f.id}
              className={cn('border-b border-border', f.id === current && 'bg-subtle')}
            >
              <td className="px-da-lg py-[9px] text-foreground da-body-strong">
                <button
                  type="button"
                  aria-pressed={f.id === current}
                  onClick={() => onSelect(f.id)}
                  className="text-left hover:underline"
                >
                  {f.name}
                </button>
              </td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-caption">
                {f.source_text}
              </td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">{f.lag_text}</td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">{f.correlation_text}</td>
              <td
                className={cn(
                  'px-da-lg py-[9px] da-mono',
                  f.significant ? 'text-foreground' : 'text-muted-foreground',
                )}
              >
                {f.test_text}
              </td>
              <td className="px-da-lg py-[9px]">
                <StatusDot tone={v.tone}>{v.label}</StatusDot>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const W = 300;
const H = 140;
const line = (points: number[]) =>
  points
    .map(
      (p, i) =>
        `${((i / Math.max(points.length - 1, 1)) * W).toFixed(1)},${((1 - p) * (H - 4) + 2).toFixed(1)}`,
    )
    .join(' ');

/* Two indexed series on one scale (no second axis), legend below, the agent's reading under it. */
function Overlay({ overlay }: { overlay: NonNullable<ExternalFactor['overlay']> }) {
  return (
    <>
      <h2 className="text-foreground da-heading-3">{overlay.title}</h2>
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${overlay.factor_label} và ${overlay.metric_label}, chỉ số hoá cùng thang`}
        className="max-w-full"
      >
        <polyline
          points={line(overlay.factor)}
          fill="none"
          strokeWidth={2}
          className="stroke-chart-2"
        >
          <title>{overlay.factor_label}</title>
        </polyline>
        <polyline
          points={line(overlay.metric)}
          fill="none"
          strokeWidth={2}
          className="stroke-chart-3"
        >
          <title>{overlay.metric_label}</title>
        </polyline>
      </svg>
      <div className="flex flex-wrap items-center gap-da-lg">
        <span className="inline-flex items-center gap-da-sm text-muted-foreground da-caption">
          <span aria-hidden className="h-[2px] w-3 bg-chart-2" />
          {overlay.factor_label}
        </span>
        <span className="inline-flex items-center gap-da-sm text-muted-foreground da-caption">
          <span aria-hidden className="h-[2px] w-3 bg-chart-3" />
          {overlay.metric_label}
        </span>
      </div>
      <p className="text-foreground da-body-small">{overlay.note}</p>
    </>
  );
}
