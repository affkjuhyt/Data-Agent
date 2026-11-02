import { useState } from 'react';
import { Icon } from '../../../../design-system/Icon';
import { RunSql } from '../RunSql';

/** "SQL đã chạy", folded until asked for; the text is read through its query run (RunSql). */
export function SqlBlock({ queryRunId }: { queryRunId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-da-xs">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex w-fit items-center gap-da-xs text-muted-foreground da-label hover:text-foreground"
      >
        <Icon name="database" size={14} />
        SQL đã chạy
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
      </button>
      {open && <RunSql queryRunId={queryRunId} />}
    </div>
  );
}
