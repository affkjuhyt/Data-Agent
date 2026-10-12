import type { ReactNode } from 'react';

/** A caption label on the left at a fixed width, the value on the right ("KV" in the P03 frames). */
export function KeyValue({
  label,
  width,
  children,
}: {
  label: string;
  width: 80 | 90 | 120;
  children: ReactNode;
}) {
  const w = { 80: 'w-[80px]', 90: 'w-[90px]', 120: 'w-[120px]' }[width];
  return (
    <div className="flex items-start gap-da-md">
      <span className={`${w} shrink-0 text-muted-foreground da-caption`}>{label}</span>
      <span className="min-w-0 flex-1 text-foreground da-body-small">{children}</span>
    </div>
  );
}
