import type { ReactNode } from 'react';
import { cn } from '../lib/utils';

/*
 * Figma "Badge" (2015:99). Tone carries meaning (Success/Warning/Error/Info/Accent/Neutral),
 * but colour is never the only signal: the label says it in words.
 * v3.2 rule: at most one badge per block; other statuses use StatusDot.
 */
type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'error' | 'info';

const TONE: Record<BadgeTone, string> = {
  neutral: 'bg-subtle text-muted-foreground',
  accent: 'bg-accent text-primary',
  success: 'bg-success-subtle text-success',
  warning: 'bg-warning-subtle text-warning',
  error: 'bg-error-subtle text-error',
  info: 'bg-info-subtle text-info',
};

export function Badge({
  tone = 'neutral',
  className,
  children,
}: {
  tone?: BadgeTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-da-xs whitespace-nowrap rounded-full px-da-md py-da-xs da-label',
        TONE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
