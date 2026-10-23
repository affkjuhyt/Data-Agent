import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { RunStep } from '../../design-system/RunStep';
import { resolveHitl } from '../../lib/v1/api';
import {
  getForecastJob,
  type ForecastAssumptionRequest,
  type ForecastJob,
  type ForecastJobStep,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { DataTable } from './parts';

/*
 * "P15 · Hỏi đáp · Dự báo đang tổng hợp" (2391:14495, UC-15-01): a forecast question with no
 * valid forecast is answered asynchronously. The answer card shows the understood intent, the
 * job's steps and an older, labelled forecast for the trend; on the right the forecasting agent
 * asks for the future value of an exogenous variable. While the job runs the page polls, and
 * once it is done it links to the result. Nothing unbacktested is shown (BR-159).
 */
const POLL_MS = 4000;

const STEP_STATE: Record<
  ForecastJobStep['state'],
  'done' | 'running' | 'pendingApproval' | 'waiting'
> = {
  done: 'done',
  running: 'running',
  needs_you: 'pendingApproval',
  pending: 'waiting',
};

export default function ForecastJobPage() {
  const { routeId } = useAppState();
  const job = useV1(['getForecastJob', routeId], routeId ? () => getForecastJob(routeId) : null);
  const running = job.data?.status === 'running';
  const { reload } = job;

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, reload]);

  if (!routeId) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="chat"
          title="Chưa chọn câu hỏi dự báo"
          description="Câu hỏi dự báo mở từ phiên làm việc hoặc từ thông báo khi dự báo xong."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={job} what="việc dự báo">
      {(j) => <JobBody job={j} onChanged={reload} />}
    </AsyncRegion>
  );
}

function JobBody({ job, onChanged }: { job: ForecastJob; onChanged: () => void }) {
  const { navigate } = useAppState();
  const done = job.status === 'done';
  const title = job.session?.title ?? `Việc dự báo · ${job.metric_name}`;
  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', job.session ? 'Phiên' : 'Dự báo', title]}
        title={title}
        description={
          job.session
            ? `Phiên của ${job.session.owner_name} · ${job.session.question_count} câu hỏi · câu hỏi về tương lai được trả lời bất đồng bộ khi chưa có dự báo còn hiệu lực`
            : 'Câu hỏi về tương lai được trả lời bất đồng bộ khi chưa có dự báo còn hiệu lực'
        }
      />
      <div className="flex flex-wrap items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <div className="flex min-w-[480px] flex-1 flex-col gap-da-xl">
          {job.question && (
            <div className="flex justify-end">
              <p className="max-w-[448px] rounded-md bg-accent px-[14px] py-[10px] text-foreground da-body-small">
                {job.question}
              </p>
            </div>
          )}
          <Card
            size="sm"
            title={
              done
                ? 'Agent Phân tích · dự báo đã tổng hợp xong'
                : 'Agent Phân tích · đang tổng hợp dự báo'
            }
          >
            <ul className="flex flex-wrap gap-da-sm" aria-label="Ý định đã hiểu">
              {job.intent_chips.map((chip) => (
                <li key={chip}>
                  <Badge>{chip}</Badge>
                </li>
              ))}
            </ul>
            <p className="max-w-[640px] text-foreground da-body-small">{job.message}</p>
            <ol aria-label="Các bước của việc dự báo" className="flex flex-col">
              {job.steps.map((step) => (
                <li key={step.title}>
                  <RunStep
                    state={STEP_STATE[step.state]}
                    title={step.title}
                    detail={step.detail || undefined}
                    duration={step.at_text ?? undefined}
                  />
                </li>
              ))}
            </ol>
            {done && job.forecast_id && (
              <Notice
                tone="success"
                action={
                  <Button
                    kind="primary"
                    onClick={() => navigate('analysis', 'FORECAST', job.forecast_id ?? undefined)}
                  >
                    Xem kết quả dự báo
                  </Button>
                }
              >
                Dự báo đã có khoảng 80% và 95%, kèm mô hình và thời điểm cắt dữ liệu.
              </Notice>
            )}
            {job.stale_forecast && (
              <div className="flex items-center gap-da-lg rounded-sm bg-subtle px-da-lg py-[10px]">
                <p className="min-w-0 flex-1 text-foreground da-caption">
                  Cần xu hướng ngay? Dự báo cũ từ {job.stale_forecast.version_label} có sẵn, gắn
                  nhãn “{job.stale_forecast.note}”.
                </p>
                <Button onClick={() => navigate('analysis', 'FORECAST', job.stale_forecast?.id)}>
                  Xem dự báo cũ
                </Button>
              </div>
            )}
          </Card>
        </div>
        <aside className="flex w-[400px] shrink-0 flex-col gap-da-xl" aria-label="Cần bạn">
          {job.assumption_request && (
            <AssumptionCard
              request={job.assumption_request}
              onEditVariables={() => navigate('analysis', 'FORECAST_STUDIO', job.id)}
              onAnswered={onChanged}
            />
          )}
          {job.status === 'waiting_human' && !job.assumption_request && (
            <Notice
              tone="warning"
              action={<Button onClick={() => navigate('needs-you')}>Mở Cần bạn</Button>}
            >
              Agent Dự báo đang chờ bạn xác nhận giả định trong Cần bạn.
            </Notice>
          )}
          <p className="text-muted-foreground da-caption">
            Câu hỏi dự báo chưa có dự báo còn hiệu lực được trả lời bất đồng bộ; không trả con số
            chưa qua backtest (BR-159). Chỉ dùng dữ liệu đã công bố trước thời điểm cắt (BR-157).
          </p>
        </aside>
      </div>
    </div>
  );
}

function AssumptionCard({
  request,
  onEditVariables,
  onAnswered,
}: {
  request: ForecastAssumptionRequest;
  onEditVariables: () => void;
  onAnswered: () => void;
}) {
  const [option, setOption] = useState(
    () => request.options.find((o) => o.is_default)?.id ?? request.options[0]?.id ?? '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const confirm = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await resolveHitl(request.hitl_id, { decision: 'answer', option });
      onAnswered();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <Card size="sm" title="Cần bạn xác nhận giả định" className="border-warning">
      <fieldset className="flex flex-col gap-da-lg">
        <legend className="mb-da-lg text-foreground da-body-small">{request.question}</legend>
        {request.options.map((o) => (
          <Radio
            key={o.id}
            name={`assumption-${request.hitl_id}`}
            value={o.id}
            checked={option === o.id}
            onChange={setOption}
          >
            {o.label}
          </Radio>
        ))}
      </fieldset>
      <DataTable
        label="Giả định của các biến khác"
        rows={request.other_assumptions}
        rowKey={(r) => r.variable}
        columns={[
          { head: 'Biến khác', width: 'w-1/2', cell: (r) => r.variable },
          { head: 'Giả định', cell: (r) => r.assumption },
        ]}
      />
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex justify-end gap-da-md">
        <Button onClick={onEditVariables}>Sửa bộ biến</Button>
        <Button kind="primary" loading={busy} disabled={!option} onClick={confirm}>
          Xác nhận và chạy
        </Button>
      </div>
    </Card>
  );
}
