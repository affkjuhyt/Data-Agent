import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  discardMetricOverride,
  getMetricOverride,
  submitMetricOverride,
  trialMetricOverride,
  type MetricOverride,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Hiệu chỉnh chỉ số theo khách" (2286:17580, UC-09-03): a pack metric the organisation
 * adjusted — the pack's definition beside its own, each change with its reason, and what happens
 * when the pack updates the original (the change is merged; a clash is decided at the upgrade).
 * Both are computed on the last 6 periods before the adjustment goes for certification, which it
 * needs again.
 */
const STATUS = {
  draft: { label: 'Đã hiệu chỉnh', tone: 'info' },
  submitted: { label: 'Chờ chứng nhận', tone: 'warning' },
  certified: { label: 'Đã chứng nhận', tone: 'success' },
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
      {(o) => <OverrideBody override={o} onChanged={override.reload} />}
    </AsyncRegion>
  );
}

function OverrideBody({
  override,
  onChanged,
}: {
  override: MetricOverride;
  onChanged: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState<'trial' | 'submit' | 'discard' | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [error, setError] = useState<string>();
  const status = STATUS[override.status];
  const open = override.status === 'draft';

  const act = async (
    key: NonNullable<typeof busy>,
    call: () => Promise<unknown>,
    message: string,
  ) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      if (key === 'discard') navigate('data', 'PACKS');
      else onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      setConfirmDiscard(false);
    }
  };

  const submitButton = (
    <Button
      kind="primary"
      loading={busy === 'submit'}
      disabled={busy !== null || !override.trial}
      onClick={() =>
        act(
          'submit',
          () => submitMetricOverride(override.id),
          `Đã gửi ${override.metric_code} chờ chứng nhận`,
        )
      }
    >
      Gửi chứng nhận
    </Button>
  );

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
        actions={
          open && (
            <>
              <Button kind="ghost" disabled={busy !== null} onClick={() => setConfirmDiscard(true)}>
                Bỏ hiệu chỉnh
              </Button>
              <Button
                loading={busy === 'trial'}
                disabled={busy !== null}
                onClick={() =>
                  act('trial', () => trialMetricOverride(override.id), 'Đã chạy thử 6 kỳ')
                }
              >
                Chạy thử 6 kỳ
              </Button>
              {override.trial ? (
                submitButton
              ) : (
                <Tooltip content="Chạy thử 6 kỳ trước khi gửi chứng nhận.">{submitButton}</Tooltip>
              )}
            </>
          )
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col px-da-3xl pb-da-2xl">
          {error && (
            <div className="pt-da-lg">
              <Notice tone="error">Không thực hiện được: {error}</Notice>
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
          <h2 className="text-foreground da-heading-3">Chạy thử 6 kỳ</h2>
          {override.trial ? (
            <>
              <table className="w-full border-collapse da-body-small">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="px-da-md py-da-sm font-normal">Kỳ</th>
                    <th className="px-da-md py-da-sm font-normal">Gốc</th>
                    <th className="px-da-md py-da-sm font-normal">Tổ chức</th>
                  </tr>
                </thead>
                <tbody>
                  {override.trial.map((t) => (
                    <tr key={t.period} className="border-b border-border">
                      <td className="px-da-md py-[9px] text-muted-foreground da-mono">
                        {t.period}
                      </td>
                      <td className="px-da-md py-[9px] text-muted-foreground da-mono">{t.base}</td>
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
              Chưa chạy thử. Chạy thử tính cả hai bản trên 6 kỳ gần nhất để so.
            </p>
          )}
          <p className="text-muted-foreground da-caption">Hiệu chỉnh phải được chứng nhận lại.</p>
        </aside>
      </div>
      <ConfirmDialog
        open={confirmDiscard}
        title={`Bỏ hiệu chỉnh ${override.metric_code}?`}
        confirmLabel="Bỏ hiệu chỉnh"
        cancelLabel="Quay lại"
        tone="danger"
        loading={busy === 'discard'}
        onCancel={() => setConfirmDiscard(false)}
        onConfirm={() =>
          act(
            'discard',
            () => discardMetricOverride(override.id),
            `${override.metric_code} theo lại bản gốc của gói`,
          )
        }
      >
        Chỉ số quay về định nghĩa trong gói. Artifact và agent đang dùng bản của tổ chức sẽ tính
        theo bản gốc từ kỳ sau.
      </ConfirmDialog>
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
