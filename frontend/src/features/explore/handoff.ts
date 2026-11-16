import type { ExploreModel } from '../../lib/v1/explore';
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
  /** The cuts read at a grain (month, quarter): Khám phá has no grain, so they stay behind. */
  time_cuts: string[];
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
    if (d && !handoff.time_cuts.includes(code)) dimension_ids.push(d.id);
    else left.push(code);
  }
  const filters: ShelfState['filters'] = [];
  for (const [code, values] of Object.entries(handoff.filters)) {
    const d = byCode.get(code);
    if (d && values.length) filters.push({ dimension_id: d.id, op: values.length === 1 ? 'eq' : 'in', values });
    else left.push(code);
  }
  return { shelves: { metric_ids: [handoff.metric_id], dimension_ids, filters }, left };
}
