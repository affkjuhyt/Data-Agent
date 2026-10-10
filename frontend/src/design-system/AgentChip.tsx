import { cn } from '../lib/utils';
import { Icon } from './Icon';
import { Tooltip } from './Tooltip';

/*
 * Figma "AgentChip" (2040:460): agent identity + status, Status = Running | Waiting | Idle |
 * Paused | Error. A 24px agent mark, the name in Body Strong, then a 6px dot and the status in
 * words — the status is never colour alone. Clicking opens the agent's current run.
 */
type AgentStatus = 'running' | 'waiting' | 'idle' | 'paused' | 'error';

const STATUS: Record<AgentStatus, { dot: string; label: string }> = {
  running: { dot: 'bg-primary', label: 'Đang chạy' },
  waiting: { dot: 'bg-warning', label: 'Chờ bạn' },
  idle: { dot: 'bg-border-strong', label: 'Chờ lịch' },
  paused: { dot: 'bg-muted-foreground', label: 'Tạm dừng' },
  error: { dot: 'bg-error', label: 'Lỗi' },
};

export function AgentChip({
  name,
  status,
  statusLabel,
  detail,
  onOpen,
  className,
}: {
  name: string;
  status: AgentStatus;
  /** Overrides the default status word, e.g. "Chờ bạn chọn". */
  statusLabel?: string;
  /** Tooltip with the current step and cost. */
  detail?: string;
  onOpen?: () => void;
  className?: string;
}) {
  const s = STATUS[status];
  const Root = onOpen ? 'button' : 'span';
  const chip = (
    <Root
      type={onOpen ? 'button' : undefined}
      onClick={onOpen}
      className={cn(
        'inline-flex items-center gap-da-sm text-left',
        onOpen && 'rounded-md hover:bg-subtle',
        className,
      )}
    >
      <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground">
        <Icon name="agent" />
      </span>
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-foreground da-body-strong">{name}</span>
        <span className="inline-flex items-center gap-da-xs text-muted-foreground da-caption">
          <span className={cn('size-1.5 shrink-0 rounded-full', s.dot)} aria-hidden />
          {statusLabel ?? s.label}
        </span>
      </span>
    </Root>
  );
  return detail ? <Tooltip content={detail}>{chip}</Tooltip> : chip;
}
