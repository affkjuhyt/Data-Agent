import type { ReactNode } from 'react';
import { Badge } from '../../design-system/Badge';
import { StatusDot } from '../../design-system/StatusDot';
import type { Fact } from '../../lib/v1/documents';
import { cn } from '../../lib/utils';

/* Small pieces shared by the P05 viewers and panels. */

/** "Phân loại   Khiếu nại phí · …": a muted caption label in a fixed column, the value beside it. */
export function KeyValue({
  label,
  children,
  labelWidth = 'w-[90px]',
}: {
  label: string;
  children: ReactNode;
  labelWidth?: string;
}) {
  return (
    <div className="flex items-start gap-da-md">
      <span className={cn('shrink-0 pt-px text-muted-foreground da-caption', labelWidth)}>
        {label}
      </span>
      <span className="min-w-0 flex-1 text-foreground da-body-small">{children}</span>
    </div>
  );
}

/** An extracted fact: plain text, a status dot, or a neutral badge, as the agent marked it. */
export function FactValue({ fact }: { fact: Fact }) {
  if (fact.badge) return <Badge>{fact.value}</Badge>;
  if (fact.tone) return <StatusDot tone={fact.tone}>{fact.value}</StatusDot>;
  return <>{fact.value}</>;
}

/** The right-hand column of the P05 screens: card background, left border. */
export function SidePanel({
  label,
  width,
  children,
  className,
}: {
  label: string;
  width: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <aside
      aria-label={label}
      className={cn(
        'flex shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1023px]:hidden',
        width,
        className,
      )}
    >
      {children}
    </aside>
  );
}

/** A plain bordered table head cell / row, as the P05 tables draw them (subtle head, caption). */
export const HEAD_ROW = 'bg-subtle text-left text-muted-foreground da-caption';
export const ROW = 'border-t border-border';
