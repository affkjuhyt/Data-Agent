import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "RunTimeline/Step" (2016:124): State = Done | Running | PendingApproval | Error | Waiting.
 * A 24px round status mark on the matching *-subtle background, title (Body Strong),
 * a muted caption, and the duration in mono. ↑/↓ moving between steps is the list's job.
 */
type RunStepState = 'done' | 'running' | 'pendingApproval' | 'error' | 'waiting';

const STATE: Record<RunStepState, { icon: IconName; mark: string; label: string }> = {
  done: { icon: 'check', mark: 'bg-success-subtle text-success', label: 'Xong' },
  running: { icon: 'refresh', mark: 'bg-info-subtle text-info', label: 'Đang chạy' },
  pendingApproval: { icon: 'approve', mark: 'bg-warning-subtle text-warning', label: 'Chờ duyệt' },
  error: { icon: 'x', mark: 'bg-error-subtle text-error', label: 'Lỗi' },
  waiting: { icon: 'clock', mark: 'bg-subtle text-muted-foreground', label: 'Đang chờ' },
};

export function RunStep({
  state,
  title,
  detail,
  duration,
  selected = false,
  onSelect,
}: {
  state: RunStepState;
  title: string;
  detail?: string;
  /** "12,8 s"; defaults to "…" while running and "—" when there is none. */
  duration?: string;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const s = STATE[state];
  const time = duration ?? (state === 'running' ? '…' : '—');
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={state === 'running' ? 'step' : undefined}
      aria-pressed={onSelect ? selected : undefined}
      className={cn(
        'flex w-full items-start gap-da-md rounded-md py-da-sm text-left',
        onSelect && 'hover:bg-subtle',
        selected && 'bg-subtle',
      )}
    >
      <span
        className={cn(
          'inline-flex size-6 shrink-0 items-center justify-center rounded-full',
          s.mark,
        )}
      >
        <Icon
          name={s.icon}
          className={state === 'running' ? 'animate-spin' : undefined}
          label={s.label}
        />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-da-xs">
        <span className="text-foreground da-body-strong">{title}</span>
        {detail && <span className="text-muted-foreground da-caption">{detail}</span>}
      </span>
      <span className="shrink-0 text-muted-foreground da-mono">{time}</span>
    </button>
  );
}
