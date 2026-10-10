/**
 * "Vì sao tin được" — one compact block under the answer: trust label, which approved metric
 * (or model-written SQL), how many rows and tables, how fresh, any risk — and a "Xem SQL"
 * toggle. SQL stays folded by default (docs/v3/01 §5.4). Everything comes from the run frames.
 */
import { useState } from 'react';
import { Badge } from '../../../design-system/Badge';
import { Button } from '../../../design-system/Button';
import { Icon } from '../../../design-system/Icon';
import type { Evidence } from '../types';
import { runChips, type Tone } from './evidenceSummary';

const BADGE_TONE = { ok: 'success', warn: 'warning', bad: 'error', neutral: 'neutral' } as const;

export function Chip({ label, tone, title }: { label: string; tone: Tone; title?: string }) {
  return (
    <span title={title} className="inline-flex">
      <Badge tone={BADGE_TONE[tone]}>{label}</Badge>
    </span>
  );
}

export function CopyButton({ text, label = 'Sao chép' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <Button kind="ghost" size="sm" icon={done ? 'check' : 'copy'} onClick={copy} title={label}>
      {done ? 'Đã chép' : label}
    </Button>
  );
}

export function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre
      data-testid="sql-block"
      className="m-0 overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border bg-subtle px-da-lg py-da-md text-foreground da-mono"
    >
      {sql}
    </pre>
  );
}

export function EvidenceStrip({ evidence, onOpenPanel }: { evidence: Evidence; onOpenPanel?: () => void }) {
  const [openSql, setOpenSql] = useState<number | null>(null);
  if (!evidence.runs.length && !evidence.warnings.length) return null;

  return (
    <section
      data-testid="evidence-strip"
      aria-label="Vì sao tin được"
      className="flex flex-col gap-da-md rounded-md border border-border bg-background px-da-lg py-da-md"
    >
      <div className="flex items-center gap-da-sm">
        <Icon name="shield" size={14} className="text-primary" />
        <span className="text-muted-foreground da-caption">Vì sao tin được</span>
        {onOpenPanel && (
          <button type="button" className="ml-auto text-primary hover:underline da-body-strong" onClick={onOpenPanel}>
            Chi tiết
          </button>
        )}
      </div>

      {evidence.runs.map((run, i) => (
        <div key={run.id ?? i} className={i ? 'flex flex-col gap-da-sm border-t border-border pt-da-md' : 'flex flex-col gap-da-sm'}>
          <div className="flex flex-wrap items-center gap-da-sm">
            {runChips(run).map(({ key, ...c }) => <Chip key={key} {...c} />)}
            <Button
              kind="ghost"
              size="sm"
              className="ml-auto"
              aria-expanded={openSql === i}
              onClick={() => setOpenSql(openSql === i ? null : i)}
            >
              {openSql === i ? 'Ẩn SQL' : 'Xem SQL'}
              <Icon name="chevron-down" size={14} className={openSql === i ? 'rotate-180' : undefined} />
            </Button>
          </div>
          {openSql === i && (
            <div className="flex flex-col gap-da-sm">
              <SqlBlock sql={run.sql} />
              <div className="flex items-center gap-da-sm">
                <CopyButton text={run.sql} label="Sao chép SQL" />
                <span className="text-muted-foreground da-caption">Đã qua lớp chặn chỉ-đọc · có ghi nhật ký truy cập</span>
              </div>
            </div>
          )}
        </div>
      ))}

      {evidence.warnings.map((w, i) => (
        w.kind === 'assumption'
          ? <p key={i} data-testid="assumption" className="m-0 text-muted-foreground da-caption">Giả định: {w.detail}</p>
          : <p key={i} className="m-0 flex items-center gap-da-xs text-error da-caption"><Icon name="alert" size={12} />{w.detail}</p>
      ))}
    </section>
  );
}
