import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  approveModelVersion,
  getForecastModel,
  listForecastModels,
  rejectModelVersion,
  type ForecastModel,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { DataTable, Stat } from './parts';

/*
 * "P15 · Mô hình dự báo · duyệt & theo dõi" (2391:15485, UC-15-04, UC-15-06): the draft model
 * card with its backtest by horizon and interval coverage, approved or rejected (with a reason)
 * by the metric owner — never by its creator (BR-158) — next to the published version's rolling
 * accuracy. A version that degrades labels its forecasts until the new one is approved (BR-160).
 */
const CANDIDATE_STATUS: Record<NonNullable<ForecastModel['candidate']>['status'], string> = {
  pending_review: 'nháp',
  published: 'đã phát hành',
  rejected: 'bị từ chối',
};

const MONITORING_STATUS: Record<NonNullable<ForecastModel['monitoring']>['status'], string> = {
  published: 'đang phát hành',
  degraded: 'suy giảm',
  retired: 'đã thôi phát hành',
};

export default function ForecastModelPage() {
  const { routeId } = useAppState();
  // From the rail without an id: the first model of the Data Space.
  const list = useV1(routeId ? null : listForecastModels, [routeId]);
  const id = routeId || list.data?.items[0]?.id;
  const model = useV1(id ? () => getForecastModel(id) : null, [id]);
  if (!routeId && list.error) {
    return (
      <AsyncRegion state={list} what="mô hình dự báo">
        {() => null}
      </AsyncRegion>
    );
  }
  if (!routeId && list.data && list.data.items.length === 0) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="chart"
          title="Chưa có mô hình dự báo nào"
          description="Mô hình được tạo từ Xưởng dự báo khi một mô hình thắng seasonal naive."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={model} what="mô hình dự báo">
      {(m) => <ModelBody model={m} onChanged={model.reload} />}
    </AsyncRegion>
  );
}

type Deciding = 'approve' | 'reject' | null;

function ModelBody({ model, onChanged }: { model: ForecastModel; onChanged: () => void }) {
  const toast = useToast();
  const [deciding, setDeciding] = useState<Deciding>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const candidate = model.candidate;
  const pending = candidate?.status === 'pending_review';

  const close = () => {
    setDeciding(null);
    setError(undefined);
  };

  const decide = async () => {
    if (!candidate || !deciding) return;
    if (deciding === 'reject' && !reason.trim()) {
      setError('Nhập lý do trước khi từ chối.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      if (deciding === 'approve') {
        await approveModelVersion(candidate.version_id);
        toast({ tone: 'success', message: `Đã duyệt và phát hành ${candidate.version_label}` });
      } else {
        await rejectModelVersion(candidate.version_id, reason.trim());
        toast({ tone: 'success', message: `Đã từ chối ${candidate.version_label}` });
      }
      setDeciding(null);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const decisionButtons = (() => {
    if (!candidate || !pending) return null;
    const approveLabel = `Duyệt và phát hành ${candidate.version_label}`;
    if (!model.can_approve.allowed) {
      const why = model.can_approve.reason ?? 'Bạn không duyệt được mô hình này.';
      return (
        <>
          <Tooltip content={why}>
            <Button disabled>Từ chối kèm lý do</Button>
          </Tooltip>
          <Tooltip content={why}>
            <Button kind="primary" disabled>
              {approveLabel}
            </Button>
          </Tooltip>
        </>
      );
    }
    return (
      <>
        <Button onClick={() => setDeciding('reject')}>Từ chối kèm lý do</Button>
        <Button kind="primary" onClick={() => setDeciding('approve')}>
          {approveLabel}
        </Button>
      </>
    );
  })();

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={[
          'Phân tích',
          'Dự báo',
          'Mô hình',
          `${model.metric_name} · ${model.frequency_label}`,
        ]}
        title={`Mô hình dự báo · ${model.metric_name}`}
        description={model.status_text}
        actions={decisionButtons}
      />
      <div className="flex flex-wrap items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {candidate && (
          <Card
            size="sm"
            title={`Thẻ mô hình · ${candidate.version_label} · ${CANDIDATE_STATUS[candidate.status]}`}
            className="w-[660px] max-w-full"
          >
            {candidate.rejection_reason && (
              <Notice tone="warning">Lý do từ chối: {candidate.rejection_reason}</Notice>
            )}
            <dl className="flex flex-col gap-da-lg">
              {candidate.card.map((row) => (
                <div key={row.label} className="flex gap-da-md da-body-small">
                  <dt className="w-[150px] shrink-0 text-muted-foreground">{row.label}</dt>
                  <dd className="min-w-0 flex-1 text-foreground">{row.value}</dd>
                </div>
              ))}
            </dl>
            <DataTable
              label="Backtest theo tầm"
              rows={candidate.horizons}
              rowKey={(h) => h.horizon_label}
              columns={[
                { head: 'Tầm', cell: (h) => h.horizon_label },
                { head: `MASE ${candidate.version_label}`, cell: (h) => h.mase_text },
                { head: 'Seasonal naive', cell: (h) => h.baseline_text },
                { head: 'Phủ 80%', cell: (h) => h.cover80_text },
                { head: 'Phủ 95%', cell: (h) => h.cover95_text },
              ]}
            />
            <StatusDot tone="warning">
              <span className="text-foreground">{candidate.limitation}</span>
            </StatusDot>
          </Card>
        )}
        {model.monitoring && (
          <Card
            size="sm"
            title={`Theo dõi ${model.monitoring.version_label} · ${MONITORING_STATUS[model.monitoring.status]}`}
            className="min-w-[400px] flex-1"
          >
            <div className="flex flex-wrap gap-da-lg">
              {model.monitoring.stats.map((s) => (
                <Stat key={s.label} stat={s} />
              ))}
            </div>
            <DataTable
              label={`Sai số của ${model.monitoring.version_label} theo kỳ`}
              rows={model.monitoring.rows}
              rowKey={(r) => r.label}
              columns={[
                { head: 'Kỳ', cell: (r) => r.label },
                { head: 'Dự báo', cell: (r) => r.forecast_text },
                { head: 'Thực tế', cell: (r) => r.actual_text },
                { head: 'Sai số', cell: (r) => r.error_text },
                {
                  head: 'Trong 95%',
                  cell: (r) => (
                    <StatusDot tone={r.inside95 ? 'success' : 'error'}>
                      {r.inside95 ? 'Có' : 'Không'}
                    </StatusDot>
                  ),
                },
              ]}
            />
            <p className="text-muted-foreground da-caption">{model.monitoring.note}</p>
          </Card>
        )}
      </div>
      {candidate && (
        <ConfirmDialog
          open={deciding !== null}
          size="md"
          title={
            deciding === 'approve'
              ? `Duyệt và phát hành ${candidate.version_label}?`
              : `Từ chối ${candidate.version_label}?`
          }
          confirmLabel={deciding === 'approve' ? 'Duyệt và phát hành' : 'Từ chối'}
          cancelLabel="Quay lại"
          tone="primary"
          loading={busy}
          error={error}
          onCancel={close}
          onConfirm={decide}
        >
          {deciding === 'approve' ? (
            <p>
              {candidate.version_label} thay{' '}
              {model.monitoring?.version_label ?? 'phiên bản đang phát hành'} cho mọi dự báo mới của{' '}
              {model.metric_name.toLowerCase()}; phiên bản cũ thôi phát hành nhưng vẫn giữ lịch sử.
            </p>
          ) : (
            <label className="flex flex-col gap-da-xs">
              <span className="text-foreground da-label">Lý do (bắt buộc)</span>
              <textarea
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
              />
            </label>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
