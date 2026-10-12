import { useState } from 'react';
import { Badge } from '../../../design-system/Badge';
import { Icon } from '../../../design-system/Icon';
import type { VerificationFrame } from '../types';

export function VerifierBadge({ verification }: { verification: VerificationFrame }) {
  const [open, setOpen] = useState(false);
  const passed = verification.verdict === 'PASS';
  return (
    <section data-testid="verifier-badge" className="flex flex-col gap-da-sm">
      <button
        type="button"
        className="inline-flex w-fit items-center gap-da-sm"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
      >
        <Icon name="shield" size={14} className={passed ? 'text-success' : 'text-error'} />
        <Badge tone={passed ? 'success' : 'error'}>{passed ? 'Đã kiểm' : 'Cần kiểm tra'}</Badge>
        <span className="text-muted-foreground da-caption">Lần kiểm {verification.iteration}</span>
      </button>
      {open && (
        <ul className="m-0 flex list-none flex-col gap-da-xs rounded-md border border-border px-da-lg py-da-md">
          {verification.checks.map(check => (
            <li key={check.name} className="flex items-start gap-da-sm da-caption">
              <span className={check.passed ? 'text-success' : 'text-error'} aria-hidden="true">{check.passed ? '✓' : '✕'}</span>
              <span><strong>{check.name}</strong>{check.detail ? `: ${check.detail}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
