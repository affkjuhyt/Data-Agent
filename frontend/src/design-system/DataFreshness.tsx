import { cn } from '../lib/utils';
import { Tooltip } from './Tooltip';

/*
 * Figma "DataFreshness" (2016:73): State = Fresh | Stale | Unknown — an 8px dot and a caption
 * in success / warning / muted. Hovering shows the source and the as-of time.
 */
type FreshnessState = 'fresh' | 'stale' | 'unknown';

const TONE: Record<FreshnessState, { dot: string; text: string }> = {
  fresh: { dot: 'bg-success', text: 'text-success' },
  stale: { dot: 'bg-warning', text: 'text-warning' },
  unknown: { dot: 'bg-muted-foreground', text: 'text-muted-foreground' },
};

export function DataFreshness({
  state,
  label,
  detail,
}: {
  state: FreshnessState;
  label: string;
  detail?: string;
}) {
  const tone = TONE[state];
  const body = (
    <span
      className={cn('inline-flex items-center gap-da-sm da-caption', tone.text)}
      tabIndex={detail ? 0 : undefined}
    >
      <span className={cn('size-2 shrink-0 rounded-full', tone.dot)} aria-hidden />
      {label}
    </span>
  );
  return detail ? <Tooltip content={detail}>{body}</Tooltip> : body;
}
