import { useCallback, useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Drawer } from '../../design-system/Drawer';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { RunStep } from '../../design-system/RunStep';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { getTestRun, startTestRun, type TestRun, type TestStep } from '../../lib/v1/agentBuilder';

/*
 * "v3.1 · Chi tiết · Kết quả chạy thử agent" (2072:4944, UC-13-04): a 560px drawer over a 32%
 * scrim. Four checks (finished, asked a person, permission violations, test suite), the steps the
 * agent took, what it would have sent, and the failed test cases. A test run sends nothing out
 * (agent_run.is_test); activation still waits for the data owner.
 *
 * The run is polled until its report arrives; the run events stream (SSE) is not wired yet.
 */
const POLL_MS = 1500;

export function useTestRun() {
  const [run, setRun] = useState<TestRun | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();

  const start = useCallback(async (agentId: string, configVersion: number) => {
    setStarting(true);
    setError(undefined);
    try {
      setRun(await startTestRun(agentId, configVersion));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }, []);

  const pending = run !== null && run.test_report === null && run.status !== 'failed';
  useEffect(() => {
    if (!pending || !run) return;
    const timer = window.setTimeout(() => {
      getTestRun(run.id)
        .then(setRun)
        .catch((e: Error) => setError(e.message));
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [pending, run]);

  return { run, starting, error, start, close: () => setRun(null) };
}

const STEP_STATE: Record<TestStep['state'], 'done' | 'pendingApproval' | 'error'> = {
  done: 'done',
  asked: 'pendingApproval',
  error: 'error',
};

export function TestRunDrawer({
  run,
  error,
  activating,
  onActivate,
  onEdit,
  onClose,
}: {
  run: TestRun;
  error?: string;
  activating: boolean;
  onActivate: () => void;
  onEdit: () => void;
  onClose: () => void;
}) {
  const report = run.test_report;
  const seconds =
    run.started_at && run.finished_at
      ? Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
      : null;
  const subtitle = report
    ? [
        report.data_text,
        'không gửi gì ra ngoài',
        seconds !== null ? `${seconds} giây` : null,
        `${formatNumber(Number(run.cost))} đ`,
      ]
        .filter(Boolean)
        .join(' · ')
    : 'Đang chạy thử · không gửi gì ra ngoài';

  return (
    <Drawer
      open
      title={`Chạy thử · ${run.agent_name}`}
      description={subtitle}
      width="lg"
      onClose={onClose}
      footer={
        <>
          <p className="min-w-0 flex-1 self-center text-muted-foreground da-caption">
            Kích hoạt cần chủ sở hữu dữ liệu đồng ý
          </p>
          <Button kind="ghost" onClick={onEdit}>
            Sửa cấu hình
          </Button>
          <Button kind="primary" disabled={!report} loading={activating} onClick={onActivate}>
            Kích hoạt
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-da-lg">
        {!report ? (
          run.status === 'failed' ? (
            <Notice tone="error">Lượt chạy thử bị lỗi, không có kết quả.</Notice>
          ) : (
            <SkeletonRows count={5} label="Đang chạy thử…" />
          )
        ) : (
          <>
            <div className="flex gap-da-md">
              <Check label="Hoàn tất" value={report.completed ? 'Có' : 'Không'} tone={report.completed ? 'success' : 'error'} />
              <Check label="Hỏi người" value={`${report.human_questions} lần`} tone="info" />
              <Check
                label="Vi phạm quyền"
                value={String(report.policy_violations)}
                tone={report.policy_violations === 0 ? 'success' : 'error'}
              />
              <Check
                label="Bộ thử"
                value={`${report.eval_passed}/${report.eval_total} đạt`}
                tone={report.eval_passed === report.eval_total ? 'success' : 'warning'}
              />
            </div>

            <section className="flex flex-col">
              <h3 className="text-muted-foreground da-label">Agent đã làm</h3>
              {run.test_steps.map((step, i) => (
                <RunStep key={i} state={STEP_STATE[step.state]} title={step.title} detail={step.detail} duration="" />
              ))}
            </section>

            {report.preview && (
              <section className="flex flex-col gap-da-xs rounded-md border border-border px-da-lg py-da-md">
                <h3 className="text-muted-foreground da-label">{report.preview.label}</h3>
                <p className="text-foreground da-body-small">{report.preview.text}</p>
              </section>
            )}

            {report.failures.map((failure) => (
              <p key={failure} className="flex items-start gap-da-sm rounded-md border border-border px-da-md py-da-sm text-foreground da-caption">
                <Icon name="alert" className="shrink-0 text-warning" />
                {failure}
              </p>
            ))}
          </>
        )}

        {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}

      </div>
    </Drawer>
  );
}

const TONE = {
  success: 'text-success',
  info: 'text-info',
  warning: 'text-warning',
  error: 'text-error',
} as const;

function Check({ label, value, tone }: { label: string; value: string; tone: keyof typeof TONE }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-da-xs rounded-md border border-border px-da-md py-da-sm">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className={cn('da-body-strong', TONE[tone])}>{value}</span>
    </div>
  );
}
