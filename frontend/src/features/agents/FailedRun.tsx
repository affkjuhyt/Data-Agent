import { useState } from 'react';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { RunStep } from '../../design-system/RunStep';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { retryRun } from '../../lib/v1/api';
import type { AgentRun, RunPlanStep } from '../../lib/v1/types';

/*
 * "v3.1 · Trạng thái · Lượt chạy lỗi" (2076:5030, UC-13-06): a failed run says what broke and
 * that it stopped safely before anything else. One 880px column: breadcrumb and title with
 * "Lỗi · đã dừng an toàn", the error (sentence, then the system's own words in mono), what the
 * agent did beside what it did to stay safe, then the ways out — rerun now, rerun against the
 * fallback source, tell the source's admin.
 */
const STEP_STATE: Record<
  RunPlanStep['state'],
  'done' | 'running' | 'pendingApproval' | 'error' | 'waiting'
> = {
  done: 'done',
  running: 'running',
  pending_approval: 'pendingApproval',
  error: 'error',
  waiting: 'waiting',
};

export function FailedRun({
  run,
  onRetried,
}: {
  run: AgentRun;
  /** The new run started by a retry. */
  onRetried: (runId: string) => void;
}) {
  const [busy, setBusy] = useState<'now' | 'fallback' | null>(null);
  const [error, setError] = useState<string>();
  const failure = run.failure;

  const retry = async (useFallback: boolean) => {
    setBusy(useFallback ? 'fallback' : 'now');
    setError(undefined);
    try {
      const next = await retryRun(run.id, useFallback);
      onRetried(next.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex justify-center px-da-3xl">
      <div className="flex w-[880px] max-w-full flex-col gap-da-lg py-da-2xl">
        <header className="flex flex-col gap-da-xs">
          <nav aria-label="Đường dẫn" className="text-muted-foreground da-caption">
            Agents › Lượt chạy {run.code}
          </nav>
          <div className="flex items-center gap-da-md">
            <h1 className="text-foreground da-heading-1">{run.title}</h1>
            <StatusDot tone="error">Lỗi · đã dừng an toàn</StatusDot>
          </div>
        </header>

        <div
          role="alert"
          className="flex items-start gap-da-md rounded-lg border border-border px-da-xl py-da-lg"
        >
          <Icon name="alert" className="text-error" />
          <div className="flex min-w-0 flex-1 flex-col">
            <p className="text-error da-heading-3">{failure?.title ?? 'Lượt chạy dừng vì lỗi'}</p>
            <p className="break-words text-foreground da-mono">
              {failure?.detail ?? run.error ?? 'Không có thông tin lỗi.'}
            </p>
          </div>
        </div>

        <div className="flex items-start gap-da-lg max-[1023px]:flex-col">
          <section className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 className="text-muted-foreground da-label">Agent đã làm</h2>
            {(run.plan ?? []).map((step) => (
              <RunStep
                key={step.title}
                state={STEP_STATE[step.state]}
                title={step.title}
                detail={step.detail}
                duration={step.duration_text ?? ''}
              />
            ))}
          </section>
          {failure && failure.safety.length > 0 && (
            <section className="flex min-w-0 flex-1 flex-col gap-da-xs">
              <h2 className="text-muted-foreground da-label">Để an toàn, agent đã</h2>
              <ul className="flex flex-col">
                {failure.safety.map((line) => (
                  <li key={line} className="text-foreground da-body-small">
                    · {line}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {error && <Notice tone="error">Không chạy lại được: {error}</Notice>}

        <div className="flex flex-wrap items-start gap-da-sm">
          <Button
            kind="primary"
            icon="refresh"
            loading={busy === 'now'}
            disabled={busy !== null}
            onClick={() => retry(false)}
          >
            Chạy lại ngay
          </Button>
          {failure?.fallback_source && (
            <Tooltip content={`Đọc từ ${failure.fallback_source} thay cho nguồn bị lỗi.`}>
              <Button
                icon="database"
                loading={busy === 'fallback'}
                disabled={busy !== null}
                onClick={() => retry(true)}
              >
                Chạy với nguồn dự phòng
              </Button>
            </Tooltip>
          )}
          <Tooltip content="Agent đã báo quản trị nguồn qua Teams; gửi lại từ đây cần API báo sự cố nguồn.">
            <Button kind="ghost" disabled>
              Báo quản trị nguồn
            </Button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
