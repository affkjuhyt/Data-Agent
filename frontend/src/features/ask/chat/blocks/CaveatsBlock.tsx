import { useState } from 'react';
import { Icon } from '../../../../design-system/Icon';

/** "Lưu ý": what the reader should keep in mind about this answer (answer_blocks.py caveats).
 *  Closed until asked: the answer leads, its limits are one line under it with their count. */
export function CaveatsBlock({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <aside
      role="note"
      aria-label="Lưu ý"
      className="flex flex-col gap-da-xs rounded-md border border-warning/30 bg-warning-subtle px-da-md py-da-sm"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-da-xs text-left text-foreground da-label"
      >
        <Icon name="alert" size={14} className="text-warning" />
        Lưu ý ({items.length})
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} className="text-muted-foreground" />
      </button>
      {open && (
        <ul className="m-0 list-disc pl-da-lg da-body-small text-foreground">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}
