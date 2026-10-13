import { useState } from 'react';
import { Input } from '../../design-system/Input';
import type { ExploreModel } from '../../lib/v1/explore';
import { cn } from '../../lib/utils';

/*
 * The left column of "v3.1 · Khám phá" (2140:15509): a search over the fields, then the
 * certified metrics of the Data Space (ƒ, ✓) and the dimensions by entity (#). Khám phá works
 * on metrics, not tables (SCR-02), so there is no dataset picker. A field is dragged onto a shelf, or added with a
 * double click / Enter; one on a shelf sits on accent. A dimension that cannot split a chosen
 * metric is dimmed with the reason.
 */
export const FIELD_MIME = 'application/x-explore-field';

export interface FieldRef {
  kind: 'metric' | 'dimension';
  id: string;
}

export function FieldList({
  model,
  selected,
  metricIds,
  onAdd,
}: {
  model: ExploreModel;
  /** Field ids on a shelf. */
  selected: Set<string>;
  metricIds: string[];
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
      <h2 className="text-foreground da-body-strong">Chỉ số đã chứng nhận</h2>
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
