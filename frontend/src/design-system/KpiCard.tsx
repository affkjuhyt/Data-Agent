import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "KpiCard" (2016:63): State = Default | Loading | Stale | NoAccess.
 * Card radius-lg, padding 16, gap 8: caption label, DA/Metric value, the delta coloured by
 * whether the move is good or bad for this metric, then the data as-of line.
 * The delta is read as "tăng/giảm", never only through the arrow.
 */
interface KpiDelta {
  text: string;
  direction: 'up' | 'down' | 'flat';
  /** Whether this move is good for the metric (NPL going down is good). */
  good: boolean;
}

type KpiState = 'default' | 'loading' | 'stale' | 'noAccess';

export function KpiCard({
  label,
  value,
  delta,
  freshness,
  state = 'default',
  status,
  onClick,
  onRequestAccess,
  children,
  className,
}: {
  label: string;
  value?: string;
  delta?: KpiDelta;
  /** "Dữ liệu đến 30/09 · Sổ cái GL" */
  freshness?: string;
  state?: KpiState;
  /** Optional node at the right of the header, e.g. <StatusDot>Chứng nhận</StatusDot>. */
  status?: ReactNode;
  onClick?: () => void;
  onRequestAccess?: () => void;
  children?: ReactNode;
  className?: string;
}) {
  const interactive = Boolean(onClick) && state !== 'noAccess' && state !== 'loading';
  const Root = interactive ? 'button' : 'div';
  return (
    <Root
      type={interactive ? 'button' : undefined}
      onClick={interactive ? onClick : undefined}
      aria-busy={state === 'loading' || undefined}
      className={cn(
        'flex flex-col items-start gap-da-md rounded-lg border border-border bg-card p-da-xl text-left',
        interactive && 'transition-colors duration-[120ms] ease-out hover:border-border-strong',
        className,
      )}
    >
      <div className="flex w-full items-center justify-between gap-da-md">
        <span className="text-muted-foreground da-caption">{label}</span>
        {status}
      </div>

      {state === 'loading' && (
        <>
          <span className="h-6 w-[120px] animate-pulse rounded-sm bg-subtle" />
          <span className="h-3 w-[180px] animate-pulse rounded-sm bg-subtle" />
        </>
      )}

      {state === 'noAccess' && (
        <>
          <span className="inline-flex items-center gap-da-sm text-muted-foreground da-body-small">
            <Icon name="lock" />
            Bạn chưa có quyền xem chỉ số này
          </span>
          {onRequestAccess && (
            <button
              type="button"
              onClick={onRequestAccess}
              className="text-primary da-body-strong hover:underline"
            >
              Yêu cầu quyền
            </button>
          )}
        </>
      )}

      {(state === 'default' || state === 'stale') && (
        <>
          <span className="text-foreground da-metric">{value}</span>
          {delta && <KpiDeltaLine delta={delta} />}
        </>
      )}

      {children}

      {freshness && (
        <span
          className={cn(
            'inline-flex items-center gap-da-xs da-caption',
            state === 'stale' ? 'text-warning' : 'text-muted-foreground',
          )}
        >
          <Icon name="clock" />
          {freshness}
        </span>
      )}
    </Root>
  );
}

const DIRECTION_WORD = { up: 'tăng', down: 'giảm', flat: 'không đổi' } as const;

function KpiDeltaLine({ delta }: { delta: KpiDelta }) {
  const tone =
    delta.direction === 'flat'
      ? 'text-muted-foreground'
      : delta.good
        ? 'text-success'
        : 'text-error';
  return (
    <span className={cn('inline-flex items-center gap-da-xs da-body-strong', tone)}>
      {delta.direction !== 'flat' && (
        <Icon name={delta.direction === 'up' ? 'trending-up' : 'trending-down'} />
      )}
      <span className="sr-only">{DIRECTION_WORD[delta.direction]} </span>
      {delta.text}
    </span>
  );
}
