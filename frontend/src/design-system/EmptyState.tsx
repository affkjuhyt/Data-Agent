import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * The empty state of "v3.1 · Cần bạn · Trống" (2165:17055), the pattern the spec names for
 * every empty region: why it is empty, in one sentence, and one next action — no large
 * illustration. A 56px ring with the icon in primary, DA/Heading 1 title, DA/Body muted line.
 */
export function EmptyState({
  title,
  description,
  icon = 'check',
  action,
  className,
}: {
  title: string;
  description: string;
  icon?: IconName;
  /** One next step, usually a Secondary <Button>. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center gap-da-lg rounded-lg border border-border bg-card px-da-2xl py-da-3xl text-center',
        className,
      )}
    >
      <span className="inline-flex size-14 items-center justify-center rounded-full border border-border text-primary">
        <Icon name={icon} />
      </span>
      <h2 className="text-foreground da-heading-1">{title}</h2>
      <p className="max-w-xl text-muted-foreground da-body">{description}</p>
      {action}
    </div>
  );
}
