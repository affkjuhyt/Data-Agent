import type { ReactNode } from 'react';
import { cn } from '../lib/utils';

/*
 * "Chấm 7px + chữ thường" from the v3.2 visual rules: the default way to show a status.
 * As drawn in the screens (table "Trạng thái", "● Chứng nhận"): 7px dot, 6px gap, 13/18 text in
 * the default colour. The dot carries the tone; the words carry the meaning.
 */
type StatusTone = 'success' | 'warning' | 'error' | 'info' | 'primary' | 'neutral';

const DOT: Record<StatusTone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  error: 'bg-error',
  info: 'bg-info',
  primary: 'bg-primary',
  neutral: 'bg-muted-foreground',
};

const TEXT: Record<StatusTone, string> = {
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
  info: 'text-info',
  primary: 'text-primary',
  neutral: 'text-muted-foreground',
};

export function StatusDot({
  tone,
  children,
  colorText = false,
  className,
}: {
  tone: StatusTone;
  children?: ReactNode;
  /** Colour the label too (e.g. "chờ bạn" in warning). */
  colorText?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-da-sm whitespace-nowrap da-body-small',
        colorText ? TEXT[tone] : 'text-foreground',
        className,
      )}
    >
      <span className={cn('size-[7px] shrink-0 rounded-full', DOT[tone])} aria-hidden />
      {children}
    </span>
  );
}
