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

function Chip({ label, tone, title }: { label: string; tone: Tone; title?: string }) {
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
