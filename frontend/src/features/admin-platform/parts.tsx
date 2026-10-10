import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

/*
 * Pieces the admin-platform screens share.
 */

const NOTE_TONE = {
  muted: 'text-muted-foreground',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-error',
} as const;

/** The 14px-padded KPI of "Giám sát hệ thống": caption, 18px value, an 11px coloured note. */
export function MiniKpi({
  label,
  value,
  note,
  noteTone = 'muted',
}: {
  label: string;
  value: string;
  note?: string;
  noteTone?: keyof typeof NOTE_TONE;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-da-xs rounded-md border border-border bg-card p-[14px]">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-metric-small">{value}</span>
      {note && <span className={cn('text-[11px] font-medium', NOTE_TONE[noteTone])}>{note}</span>}
    </div>
  );
}

/** The DA/Metric KPI of "Vận hành agent": caption, 24px value, caption note in its tone. */
export function OpsKpi({
  label,
  value,
  note,
  noteTone = 'muted',
}: {
  label: string;
  value: string;
  note?: string;
  noteTone?: keyof typeof NOTE_TONE;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-da-xs rounded-lg border border-border bg-card p-da-lg">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-metric">{value}</span>
      {note && <span className={cn('da-caption', NOTE_TONE[noteTone])}>{note}</span>}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="font-semibold text-foreground text-[13px] leading-[18px]">{children}</h2>;
}

/** Label / value rows of the settings pages: 220px muted label, value, a bottom rule. */
export function KeyValueList({
  rows,
  mono = false,
}: {
  rows: { key: string; label: ReactNode; value: ReactNode }[];
  mono?: boolean;
}) {
  return (
    <dl className="flex flex-col gap-da-2xl px-da-3xl pt-[20px]">
      {rows.map((row) => (
        <div key={row.key} className="flex items-start gap-da-2xl border-b border-border py-[10px]">
          <dt className="w-[220px] shrink-0 text-muted-foreground da-body-small">{row.label}</dt>
          <dd className={cn('min-w-0 flex-1 text-foreground', mono ? 'da-mono' : 'da-body-small')}>
            {row.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
