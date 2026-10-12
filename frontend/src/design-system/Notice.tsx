import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * The one-line status bar under a page title ("Sức khoẻ" on "v3.1 · Dữ liệu" 2155:16326 and
 * "v3.1 · Quản trị" 2164:16368): border, radius-lg, 8×12 padding, the icon in the tone's colour,
 * a DA/Body sentence saying what happened and what it means, and at most one action.
 */
type NoticeTone = 'warning' | 'error' | 'info' | 'success';

const ICON: Record<NoticeTone, { name: IconName; className: string }> = {
  warning: { name: 'alert', className: 'text-warning' },
  error: { name: 'alert', className: 'text-error' },
  info: { name: 'info', className: 'text-info' },
  success: { name: 'check', className: 'text-success' },
};

export function Notice({
  tone = 'warning',
  children,
  action,
  icon: iconName,
  className,
}: {
  tone?: NoticeTone;
  children: ReactNode;
  action?: ReactNode;
  /** Replaces the tone's icon (a clock for stale data); the colour still follows the tone. */
  icon?: IconName;
  className?: string;
}) {
  const icon = { ...ICON[tone], ...(iconName ? { name: iconName } : {}) };
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn(
        'flex w-full items-center gap-[10px] rounded-lg border border-border px-da-lg py-da-md',
        className,
      )}
    >
      <Icon name={icon.name} className={icon.className} />
      <p className="min-w-0 flex-1 text-foreground da-body">{children}</p>
      {action}
    </div>
  );
}
