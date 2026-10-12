import { AgentChip } from '../../design-system/AgentChip';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Card, CardLink } from '../../design-system/Card';
import { cn } from '../../lib/utils';
import type { Page } from '../../lib/v1/client';
import type { AgentRun } from '../../lib/v1/types';
import type { V1State } from '../../lib/v1/useV1';

/*
 * "Agent đang làm gì cho bạn" of "v3.1 · Trang chủ": one row per live run (running or waiting
 * on a person), top border, 6px padding, 16px gap: the AgentChip, the latest sentence of its
 * narrative (the step under way) in DA/Body over a 6px progress bar (primary, warning while it waits), and the ETA.
 */
export function AgentActivity({
  runs,
  onOpenAll,
}: {
  runs: V1State<Page<AgentRun>>;
  onOpenAll: () => void;
}) {
  const live = (runs.data?.items ?? []).filter(
    (r) => r.status === 'running' || r.status === 'waiting_human',
  );
  if (runs.data && live.length === 0) return null;
  return (
    <Card
      title="Agent đang làm gì cho bạn"
      action={<CardLink onClick={onOpenAll}>Xem tất cả</CardLink>}
    >
      <AsyncRegion state={runs} what="lượt chạy">
        {() => (
          <ul className="flex flex-col">
            {live.map((run) => {
              const waiting = run.status === 'waiting_human';
              const sentence =
                (
                  run.narrative.find((e) => e.state === 'now') ??
                  run.narrative[run.narrative.length - 1]
                )?.text ?? 'Đang chạy';
              const progress =
                run.total_steps && run.current_step
                  ? Math.min(1, run.current_step / run.total_steps)
                  : null;
              return (
                <li
                  key={run.id}
                  className="flex items-center gap-da-xl border-t border-border py-da-sm max-[767px]:flex-wrap max-[767px]:gap-da-sm"
                >
                  <AgentChip
                    name={run.agent_name}
                    status={waiting ? 'waiting' : 'running'}
                    className="w-[170px] shrink-0"
                  />
                  <div className="flex min-w-0 flex-1 flex-col gap-da-sm max-[767px]:order-last max-[767px]:basis-full">
                    <span className="text-foreground da-body">{sentence}</span>
                    {progress !== null && (
                      <span
                        className="h-1.5 w-full overflow-hidden rounded-lg bg-subtle"
                        role="progressbar"
                        aria-valuenow={Math.round(progress * 100)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`Tiến độ ${run.agent_name}`}
                      >
                        <span
                          className={cn(
                            'block h-full rounded-[3px]',
                            waiting ? 'bg-warning' : 'bg-primary',
                          )}
                          style={{ width: `${progress * 100}%` }}
                        />
                      </span>
                    )}
                  </div>
                  <span
                    className={cn(
                      'shrink-0 da-caption',
                      waiting ? 'text-warning' : 'text-muted-foreground',
                    )}
                  >
                    {waiting ? 'chờ bạn' : (run.eta_text ?? '')}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </AsyncRegion>
    </Card>
  );
}
