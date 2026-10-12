import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

/*
 * Page header of the v3.1 screens: an optional breadcrumb (caption, muted), the title in
 * DA/Heading 1 with actions on the right (at most one Primary), and one muted line below.
 * Detail screens draw it with a bottom border and 20/16 × 32 padding ("P13 · Đội agent");
 * area landings ("v3.1 · Phân tích") draw it without a border, inside the page padding.
 */
export function PageHeader({
  title,
  description,
  breadcrumb,
  actions,
  bordered = false,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  breadcrumb?: string[];
  actions?: ReactNode;
  bordered?: boolean;
  className?: string;
}) {
  return (
    <header
      className={cn(
        'flex flex-col gap-da-xs',
        bordered && 'border-b border-border px-da-3xl pb-da-xl pt-[20px]',
        className,
      )}
    >
      {breadcrumb && breadcrumb.length > 0 && (
        <nav aria-label="Đường dẫn" className="text-muted-foreground da-caption">
          {breadcrumb.join(' / ')}
        </nav>
      )}
      <div className="flex items-center gap-[10px]">
        <h1 className="text-foreground da-heading-1">{title}</h1>
        <span className="flex-1" />
        {actions && <div className="flex shrink-0 items-center gap-da-md">{actions}</div>}
      </div>
      {description && <p className="text-muted-foreground da-body-small">{description}</p>}
    </header>
  );
}
