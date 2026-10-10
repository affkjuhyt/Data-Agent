import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Icon } from '../../design-system/Icon';
import type { ExploreDimension, ExploreFilter, ExploreModel } from '../../lib/v1/explore';
import { cn } from '../../lib/utils';
import { FormDialog } from '../sessions/FormDialog';
import { FIELD_MIME, type FieldRef } from './FieldList';
import { filterText } from './format';

/*
 * The shelves of "v3.1 · Khám phá" (2140:15603): Chỉ số (accent pills), Chiều (info-subtle
 * pills), Lọc (subtle pills "Phân khúc = Bán lẻ"), each ending in a dashed "+ kéo vào đây" drop
 * target. The drop target is also a button listing the fields that fit, for keyboard users.
 * A dimension dropped on Lọc, or a filter pill clicked, opens the value picker.
 */
export interface ShelfState {
  metric_ids: string[];
  dimension_ids: string[];
  filters: ExploreFilter[];
}

type Shelf = 'metric' | 'dimension' | 'filter';

export function Shelves({
  model,
  state,
  onChange,
}: {
  model: ExploreModel;
  state: ShelfState;
  onChange: (next: ShelfState) => void;
}) {
  const [editing, setEditing] = useState<ExploreDimension | null>(null);
  const metric = (id: string) => model.metrics.find((m) => m.id === id);
  const dimension = (id: string) => model.dimensions.find((d) => d.id === id);

  const drop = (shelf: Shelf, field: FieldRef) => {
    if (shelf === 'metric' && field.kind === 'metric' && !state.metric_ids.includes(field.id))
      onChange({ ...state, metric_ids: [...state.metric_ids, field.id] });
    if (
      shelf === 'dimension' &&
      field.kind === 'dimension' &&
      !state.dimension_ids.includes(field.id)
    )
      onChange({ ...state, dimension_ids: [...state.dimension_ids, field.id] });
    if (shelf === 'filter' && field.kind === 'dimension') {
      const dim = dimension(field.id);
      if (dim) setEditing(dim);
    }
  };

  const choices = (shelf: Shelf): { field: FieldRef; label: string }[] =>
    shelf === 'metric'
      ? model.metrics
          .filter((m) => !state.metric_ids.includes(m.id))
          .map((m) => ({ field: { kind: 'metric', id: m.id }, label: m.label }))
      : model.dimensions
          .filter((d) =>
            shelf === 'dimension'
              ? !state.dimension_ids.includes(d.id)
              : !state.filters.some((f) => f.dimension_id === d.id),
          )
          .map((d) => ({ field: { kind: 'dimension', id: d.id }, label: d.label }));

  return (
    <div className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-lg py-da-md">
      <ShelfRow label="Chỉ số" shelf="metric" choices={choices('metric')} onDrop={drop}>
        {state.metric_ids.map((id) => (
          <Pill
            key={id}
            tone="metric"
            label={metric(id)?.label ?? id}
            onRemove={() =>
              onChange({ ...state, metric_ids: state.metric_ids.filter((x) => x !== id) })
            }
          />
        ))}
      </ShelfRow>
      <ShelfRow label="Chiều" shelf="dimension" choices={choices('dimension')} onDrop={drop}>
        {state.dimension_ids.map((id) => (
          <Pill
            key={id}
            tone="dimension"
            label={dimension(id)?.label ?? id}
            onRemove={() =>
              onChange({ ...state, dimension_ids: state.dimension_ids.filter((x) => x !== id) })
            }
          />
        ))}
      </ShelfRow>
      <ShelfRow label="Lọc" shelf="filter" choices={choices('filter')} onDrop={drop}>
        {state.filters.map((f) => {
          const dim = dimension(f.dimension_id);
          return (
            <Pill
              key={f.dimension_id}
              tone="filter"
              caption={dim?.label ?? f.dimension_id}
              label={filterText(f, dim)}
              onOpen={dim ? () => setEditing(dim) : undefined}
              onRemove={() =>
                onChange({
                  ...state,
                  filters: state.filters.filter((x) => x.dimension_id !== f.dimension_id),
                })
              }
            />
          );
        })}
      </ShelfRow>

      {editing && (
        <FilterDialog
          dimension={editing}
          current={state.filters.find((f) => f.dimension_id === editing.id)}
          onClose={() => setEditing(null)}
          onApply={(values) => {
            const others = state.filters.filter((f) => f.dimension_id !== editing.id);
            onChange({
              ...state,
              filters:
                values.length === 0
                  ? others
                  : [
                      ...others,
                      { dimension_id: editing.id, op: values.length === 1 ? 'eq' : 'in', values },
                    ],
            });
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function ShelfRow({
  label,
  shelf,
  choices,
  onDrop,
  children,
}: {
  label: string;
  shelf: Shelf;
  choices: { field: FieldRef; label: string }[];
  onDrop: (shelf: Shelf, field: FieldRef) => void;
  children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const accepts = (e: DragEvent) => e.dataTransfer.types.includes(FIELD_MIME);
  return (
    <div
      role="group"
      aria-label={`Kệ ${label}`}
      className={cn('flex flex-wrap items-center gap-da-md rounded-md', over && 'bg-subtle')}
      onDragOver={(e) => {
        if (!accepts(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        setOver(false);
        const raw = e.dataTransfer.getData(FIELD_MIME);
        if (!raw) return;
        e.preventDefault();
        try {
          onDrop(shelf, JSON.parse(raw) as FieldRef);
        } catch {
          // A drag from outside the field list carries something else; ignore it.
        }
      }}
    >
      <span className="w-16 shrink-0 text-muted-foreground da-label">{label}</span>
      {children}
      <AddTarget label={label} choices={choices} onPick={(field) => onDrop(shelf, field)} />
    </div>
  );
}

function AddTarget({
  label,
  choices,
  onPick,
}: {
  label: string;
  choices: { field: FieldRef; label: string }[];
  onPick: (field: FieldRef) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  return (
    <div ref={ref} className="relative" onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
      <button
        type="button"
        aria-label={`Thêm vào kệ ${label}`}
        aria-expanded={open}
        disabled={choices.length === 0}
        onClick={() => setOpen((o) => !o)}
        className="rounded-full border border-dashed border-border-strong px-da-md py-da-xs text-muted-foreground da-caption hover:text-foreground"
      >
        + kéo vào đây
      </button>
      {open && (
        <ul
          role="menu"
          aria-label={`Thêm vào kệ ${label}`}
          className="absolute left-0 top-full z-[var(--z-popover)] mt-da-xs flex max-h-72 w-56 flex-col overflow-y-auto rounded-md border border-border bg-card p-da-xs shadow-md"
        >
          {choices.map((c) => (
            <li key={c.field.id}>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onPick(c.field);
                }}
                className="w-full rounded-sm px-da-md py-da-sm text-left text-foreground da-body-small hover:bg-subtle"
              >
                {c.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const PILL_TONE = {
  metric: 'bg-accent rounded-full',
  dimension: 'bg-info-subtle rounded-full',
  filter: 'bg-subtle rounded-lg',
} as const;

function Pill({
  tone,
  label,
  caption,
  onRemove,
  onOpen,
}: {
  tone: keyof typeof PILL_TONE;
  label: string;
  caption?: string;
  onRemove: () => void;
  onOpen?: () => void;
}) {
  const name = caption ? `${caption} ${label}` : label;
  const body = (
    <>
      {caption && <span className="text-muted-foreground da-caption">{caption}</span>}
      <span className="text-foreground da-body-strong">{label}</span>
    </>
  );
  return (
    <span className={cn('inline-flex items-center gap-da-sm px-da-md py-da-xs', PILL_TONE[tone])}>
      {onOpen ? (
        <button
          type="button"
          onClick={onOpen}
          aria-label={`Sửa lọc ${name}`}
          className="inline-flex items-center gap-da-sm"
        >
          {body}
        </button>
      ) : (
        body
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Bỏ ${name}`}
        className="inline-flex text-muted-foreground hover:text-foreground"
      >
        <Icon name="x" />
      </button>
    </span>
  );
}

function FilterDialog({
  dimension,
  current,
  onClose,
  onApply,
}: {
  dimension: ExploreDimension;
  current: ExploreFilter | undefined;
  onClose: () => void;
  onApply: (values: string[]) => void;
}) {
  const [values, setValues] = useState<string[]>(current?.values ?? []);
  return (
    <FormDialog
      open
      title={`Lọc theo ${dimension.label.toLowerCase()}`}
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" onClick={() => onApply(values)}>
            Áp dụng
          </Button>
        </>
      }
    >
      <div className="flex max-h-80 flex-col gap-da-sm overflow-y-auto">
        {dimension.values.map((v) => (
          <Checkbox
            key={v.value}
            checked={values.includes(v.value)}
            onChange={(on) =>
              setValues((prev) =>
                on
                  ? dimension.values
                      .map((x) => x.value)
                      .filter((x) => x === v.value || prev.includes(x))
                  : prev.filter((x) => x !== v.value),
              )
            }
          >
            {v.label}
          </Checkbox>
        ))}
      </div>
    </FormDialog>
  );
}
