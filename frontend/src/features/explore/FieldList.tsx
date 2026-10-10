import { useEffect, useRef, useState } from 'react';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { StatusDot } from '../../design-system/StatusDot';
import type { ExploreDataset, ExploreModel } from '../../lib/v1/explore';
import { cn } from '../../lib/utils';

/*
 * The left column of "v3.1 · Khám phá" (2140:15509): the dataset picker (only certified
 * datasets the viewer may use), a search over the fields, then the metrics (ƒ, ✓ when
 * certified) and the dimensions by group (#). A field is dragged onto a shelf, or added with a
 * double click / Enter; one on a shelf sits on accent. A dimension that cannot split a chosen
 * metric is dimmed with the reason.
 */
export const FIELD_MIME = 'application/x-explore-field';

export interface FieldRef {
  kind: 'metric' | 'dimension';
  id: string;
}

export function FieldList({
  datasets,
  model,
  selected,
  metricIds,
  onDataset,
  onAdd,
}: {
  datasets: ExploreDataset[];
  model: ExploreModel;
  /** Field ids on a shelf. */
  selected: Set<string>;
  metricIds: string[];
  onDataset: (id: string) => void;
  onAdd: (field: FieldRef) => void;
}) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const match = (label: string) => !q || label.toLowerCase().includes(q);
  const metrics = model.metrics.filter((m) => match(m.label));
  const groups = new Map<string, ExploreModel['dimensions']>();
  model.dimensions
    .filter((d) => match(d.label))
    .forEach((d) => groups.set(d.group_label, [...(groups.get(d.group_label) ?? []), d]));

  return (
    <div className="flex flex-col gap-da-md">
      <DatasetPicker datasets={datasets} current={model.dataset} onSelect={onDataset} />
      <Input
        icon="search"
        placeholder="Tìm chỉ số, chiều"
        aria-label="Tìm chỉ số, chiều"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {metrics.length > 0 && (
        <FieldGroup title="Chỉ số">
          {metrics.map((m) => (
            <FieldItem
              key={m.id}
              field={{ kind: 'metric', id: m.id }}
              label={m.label}
              certified={m.certified}
              active={selected.has(m.id)}
              onAdd={onAdd}
            />
          ))}
        </FieldGroup>
      )}
      {[...groups].map(([group, dims]) => (
        <FieldGroup key={group} title={`Chiều · ${group}`}>
          {dims.map((d) => {
            const clash = d.incompatible_metric_ids.some((id) => metricIds.includes(id));
            return (
              <FieldItem
                key={d.id}
                field={{ kind: 'dimension', id: d.id }}
                label={d.label}
                active={selected.has(d.id)}
                unavailable={clash ? (d.incompatible_reason ?? undefined) : undefined}
                onAdd={onAdd}
              />
            );
          })}
        </FieldGroup>
      ))}
      {metrics.length === 0 && groups.size === 0 && (
        <p className="px-da-sm text-muted-foreground da-caption">Không có trường nào khớp.</p>
      )}
    </div>
  );
}

function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-md" aria-label={title}>
      <h3 className="uppercase text-muted-foreground da-overline">{title}</h3>
      <ul className="flex flex-col gap-da-md">{children}</ul>
    </section>
  );
}

function FieldItem({
  field,
  label,
  certified,
  active,
  unavailable,
  onAdd,
}: {
  field: FieldRef;
  label: string;
  certified?: boolean;
  active: boolean;
  unavailable?: string;
  onAdd: (field: FieldRef) => void;
}) {
  return (
    <li>
      <button
        type="button"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData(FIELD_MIME, JSON.stringify(field));
          e.dataTransfer.effectAllowed = 'copy';
        }}
        onDoubleClick={() => onAdd(field)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onAdd(field);
          }
        }}
        title={unavailable}
        aria-pressed={active}
        aria-description={unavailable}
        className={cn(
          'flex w-full cursor-grab items-center gap-da-md rounded-sm px-da-sm py-da-xs text-left',
          active ? 'bg-accent' : 'hover:bg-subtle',
          unavailable && 'opacity-60',
        )}
      >
        <span className="text-border-strong da-mono" aria-hidden>
          ⋮⋮
        </span>
        <span
          className={cn('da-mono', field.kind === 'metric' ? 'text-primary' : 'text-info')}
          aria-hidden
        >
          {field.kind === 'metric' ? 'ƒ' : '#'}
        </span>
        <span
          className={cn(
            'min-w-0 flex-1 truncate',
            active ? 'text-primary da-body-strong' : 'text-foreground da-body-small',
          )}
        >
          {label}
        </span>
        {certified && (
          <span className="text-muted-foreground da-caption" aria-label="đã chứng nhận">
            ✓
          </span>
        )}
      </button>
    </li>
  );
}

function DatasetPicker({
  datasets,
  current,
  onSelect,
}: {
  datasets: ExploreDataset[];
  current: ExploreDataset;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const choices = datasets.filter((d) => d.certified);

  return (
    <div ref={ref} className="relative" onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Bộ dữ liệu: ${current.display_name}`}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full flex-col gap-[2px] rounded-md border border-border-strong px-da-md py-da-sm text-left"
      >
        <span className="flex w-full items-center">
          <span className="min-w-0 flex-1 truncate text-foreground da-body-strong">
            {current.display_name}
          </span>
          <Icon name="chevron-down" className="text-muted-foreground" />
        </span>
        <span className="flex items-center gap-da-sm">
          {current.certified && <StatusDot tone="success">Chứng nhận</StatusDot>}
          <span className="text-muted-foreground da-caption">{current.data_until_text}</span>
        </span>
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Chọn bộ dữ liệu"
          className="absolute left-0 right-0 top-full z-[var(--z-popover)] mt-da-xs flex flex-col rounded-md border border-border bg-card p-da-xs shadow-md"
        >
          {choices.map((d) => (
            <li
              key={d.id}
              role="option"
              aria-selected={d.id === current.id}
              tabIndex={0}
              onClick={() => {
                setOpen(false);
                if (d.id !== current.id) onSelect(d.id);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  setOpen(false);
                  if (d.id !== current.id) onSelect(d.id);
                }
              }}
              className={cn(
                'flex cursor-pointer items-center rounded-sm px-da-md py-da-sm da-body-small',
                d.id === current.id ? 'bg-accent text-primary' : 'text-foreground hover:bg-subtle',
              )}
            >
              <span className="flex-1 truncate">{d.display_name}</span>
              {d.id === current.id && <Icon name="check" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
