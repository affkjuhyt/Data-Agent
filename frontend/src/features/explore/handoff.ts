import type { ExploreModel, Grain } from '../../lib/v1/explore';
import type { ShelfState } from './Shelves';

/*
 * "Mở trong Khám phá" from an answer (E3): the reading behind a chart or table — its certified
 * metric, cuts and filters, by code (worker_agent blocks._explore) — put on Khám phá's shelves,
 * where the reader keeps exploring it by hand. Held here between the click and Khám phá's first
 * render; a reload loses it, which leaves Khám phá empty as it always opens.
 */

export interface ExploreHandoff {
  metric_id: string;
  group_by: string[];
  filters: Record<string, string[]>;
  /** The cuts read along time: Khám phá reads them at the grain below (K1), its own time cut. */
  time_cuts: string[];
  /** The period read ("2013", "2013-Q3", "2013-09"); a range Khám phá cannot pick is left out. */
  period?: string | null;
  grain?: Grain | null;
}

let pending: ExploreHandoff | null = null;

export const handOffToExplore = (handoff: ExploreHandoff) => {
  pending = handoff;
};

/** Read without taking: StrictMode renders twice, and both renders must see it. */
export const pendingHandoff = () => pending;

export const clearHandoff = () => {
  pending = null;
};

export interface Opened {
  shelves: ShelfState;
  /** Cuts and filters that could not be put on a shelf, by code. */
  left: string[];
}

/** The shelves the hand-off fills in this model, or null when its metric is not in it. */
export function shelvesOf(handoff: ExploreHandoff, model: ExploreModel): Opened | null {
  if (!model.metrics.some((m) => m.id === handoff.metric_id)) return null;
  const byCode = new Map(model.dimensions.map((d) => [d.code, d]));
  const left: string[] = [];
  const dimension_ids: string[] = [];
  for (const code of handoff.group_by) {
    const d = byCode.get(code);
    if (handoff.time_cuts.includes(code)) {
      // Read at its grain as the metric's own time, not as a dimension cut by day.
      if (!handoff.grain) left.push(code);
    } else if (d) dimension_ids.push(d.id);
    else left.push(code);
  }
  const filters: ShelfState['filters'] = [];
  for (const [code, values] of Object.entries(handoff.filters)) {
    const d = byCode.get(code);
    if (d && values.length) filters.push({ dimension_id: d.id, op: values.length === 1 ? 'eq' : 'in', values });
    else left.push(code);
  }
  const period = handoff.period && /^\d{4}(-Q[1-4]|-\d{2})?$/.test(handoff.period) ? handoff.period : null;
  return {
    shelves: { metric_ids: [handoff.metric_id], dimension_ids, filters, period, grain: handoff.grain ?? null },
    left,
  };
}
