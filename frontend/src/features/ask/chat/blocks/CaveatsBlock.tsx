import { Icon } from '../../../../design-system/Icon';

/** "Lưu ý": what the reader should keep in mind about this answer (answer_blocks.py caveats). */
export function CaveatsBlock({ items }: { items: string[] }) {
  return (
    <aside
      role="note"
      aria-label="Lưu ý"
      className="flex flex-col gap-da-xs rounded-md border border-warning/30 bg-warning-subtle px-da-md py-da-sm"
    >
      <span className="inline-flex items-center gap-da-xs text-foreground da-label">
        <Icon name="alert" size={14} className="text-warning" />
        Lưu ý
      </span>
      <ul className="m-0 list-disc pl-da-lg da-body-small text-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </aside>
  );
}
