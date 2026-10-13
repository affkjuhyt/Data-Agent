import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { getMetricOverride, type MetricOverride } from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Hiệu chỉnh chỉ số theo khách" (2286:17580, UC-09-03): a pack metric the organisation
 * adjusted — the pack's definition beside its own, with the reason, and what happens when the pack
 * updates the original (a clash is decided at the upgrade). An override is sent for approval when
 * it is created (from Đối chiếu số); the approver, and compliance for a regulatory metric, decide
 * in Cần bạn. Its calibration run shows the organisation's formula against the filed numbers.
 */
const STATUS = {
  submitted: { label: 'Chờ duyệt', tone: 'warning' },
  certified: { label: 'Đang áp dụng', tone: 'success' },
  superseded: { label: 'Đã được thay', tone: 'neutral' },
} as const;

export default function MetricOverridePage() {
  const { routeId } = useAppState();
  const override = useV1(routeId ? () => getMetricOverride(routeId) : null, [routeId]);
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Mở hiệu chỉnh từ một gói ngành đã cài.
      </p>
    );
  return (
    <AsyncRegion state={override} what="hiệu chỉnh chỉ số">
      {(o) => <OverrideBody override={o} />}
    </AsyncRegion>
  );
}

function OverrideBody({ override }: { override: MetricOverride }) {
  const status = STATUS[override.status];

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Chỉ số', override.metric_code]}
        title={
          <span className="flex items-center gap-[10px]">
            {override.metric_code} · hiệu chỉnh cho tổ chức
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={override.summary_text}
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col px-da-3xl pb-da-2xl">
          {override.awaiting_text && (
            <div className="pt-da-lg">
              <Notice tone="info">{override.awaiting_text}</Notice>
            </div>
          )}
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <div className="flex items-baseline gap-da-md">
              <h2 className="text-foreground da-heading-3">Khác với bản gốc</h2>
              <span className="ml-auto text-muted-foreground da-caption">
                {override.changes.length} điểm khác
              </span>
            </div>
            <div className="grid grid-cols-2 items-start gap-da-md max-[1023px]:grid-cols-1">
              <Expression title="Bản gốc trong gói" text={override.base_expression} muted />
              <Expression title="Bản của tổ chức" text={override.org_expression} />
            </div>
            <table className="w-full table-fixed border-collapse da-body-small">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-md py-da-sm font-normal">Thay đổi</th>
                  <th className="px-da-md py-da-sm font-normal">Lý do</th>
                </tr>
              </thead>
              <tbody>
                {override.changes.map((c) => (
                  <tr key={c.change} className="border-b border-border text-foreground">
                    <td className="px-da-md py-[10px] da-mono">{c.change}</td>
                    <td className="px-da-md py-[10px]">{c.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Khi gói cập nhật bản gốc</h2>
            <p className="text-foreground da-body-small">{override.upgrade_note}</p>
          </section>
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Đối chiếu với số đã nộp</h2>
          {override.trial ? (
            <>
              <table className="w-full border-collapse da-body-small">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="px-da-md py-da-sm font-normal">Kỳ</th>
                    <th className="px-da-md py-da-sm font-normal">Đã nộp</th>
                    <th className="px-da-md py-da-sm font-normal">Bản tổ chức</th>
                  </tr>
                </thead>
                <tbody>
                  {override.trial.map((t) => (
                    <tr key={t.period} className="border-b border-border">
                      <td className="px-da-md py-[9px] text-muted-foreground da-mono">
                        {t.period}
                      </td>
                      <td className="px-da-md py-[9px] text-muted-foreground da-mono">
                        {t.reported}
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-mono">{t.org}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {override.trial_note && (
                <p className="text-foreground da-body-small">{override.trial_note}</p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground da-body-small">
              Chưa đối chiếu. Hiệu chỉnh được tính lại trên số đã nộp sau khi được duyệt.
            </p>
          )}
          <p className="text-muted-foreground da-caption">
            Mỗi hiệu chỉnh mới được duyệt lại; bản trước đó thôi áp dụng.
          </p>
        </aside>
      </div>
    </div>
  );
}

function Expression({ title, text, muted }: { title: string; text: string; muted?: boolean }) {
  return (
    <div className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg">
      <span className="text-muted-foreground da-caption">{title}</span>
      <pre
        className={
          muted
            ? 'm-0 whitespace-pre-wrap text-muted-foreground da-mono'
            : 'm-0 whitespace-pre-wrap text-foreground da-mono'
        }
      >
        {text}
      </pre>
    </div>
  );
}
