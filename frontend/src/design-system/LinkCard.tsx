import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';
import { Tooltip } from './Tooltip';

/*
 * The entry card of the v3.1 area landings: "Bắt đầu" on Phân tích (2155:16107), the journey
 * on Dữ liệu (2155:16341), the admin grid on Quản trị (2164:16376). Card, radius 10, 12×16
 * padding; an icon slot (no coloured box behind it, v3.2 rules) with an optional mark on the
 * right (chevron or step number); the title and the lines below it.
 * A card whose screen is not built yet says so on hover instead of going nowhere.
 */
export function LinkCard({
  icon,
  iconSlot = 36,
  title,
  titleSize = 'sm',
  corner,
  children,
  onOpen,
  pendingReason,
  className,
}: {
  icon: IconName;
  iconSlot?: 36 | 40;
  title: string;
  titleSize?: 'sm' | 'md';
  /** Right of the icon row: "chevron" or a step number. */
  corner?: 'chevron' | number;
  children?: ReactNode;
  onOpen?: () => void;
  pendingReason?: string;
  className?: string;
}) {
  const disabled = !onOpen || Boolean(pendingReason);
  const card = (
    <button
      type="button"
      onClick={disabled ? undefined : onOpen}
      aria-disabled={disabled || undefined}
      className={cn(
        'flex w-full min-w-0 flex-col items-start gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg text-left',
        disabled
          ? 'cursor-default'
          : 'transition-colors duration-[120ms] ease-out hover:border-border-strong',
        className,
      )}
    >
      <span className="flex w-full items-center">
        <span
          className="inline-flex shrink-0 items-center justify-center text-muted-foreground"
          style={{ width: iconSlot, height: iconSlot }}
        >
          <Icon name={icon} />
        </span>
        <span className="flex-1" />
        {corner === 'chevron' && <Icon name="chevron-right" className="text-border-strong" />}
        {typeof corner === 'number' && (
          <span className="text-border-strong da-metric">{corner}</span>
        )}
      </span>
      <span className={cn('text-foreground', titleSize === 'md' ? 'da-heading-2' : 'da-heading-3')}>
        {title}
      </span>
      {children}
    </button>
  );
  return pendingReason ? (
    <Tooltip content={pendingReason} className="flex w-full min-w-0">
      {card}
    </Tooltip>
  ) : (
    card
  );
}
