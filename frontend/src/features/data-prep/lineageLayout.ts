import type { LineageGraph, LineageNode } from '../../lib/v1/dataPrep';

/*
 * Lays out a lineage graph from the API, left to right as in "P06 · Dòng dữ liệu" (2290:17979):
 * everything the focus is built from sits in columns by its longest distance from a root, the
 * focus in the last of them, and what uses the focus stacks above and below it in the same
 * column. A node sits at the average height of its inputs (roots in order, 160px apart), pushed
 * down when it would overlap a neighbour. Edges are elbows: right, down/up 20px before the
 * target, right again; consumer edges are vertical. Nothing here is drawn from fixed positions.
 */
export const NODE_W = 140;
export const NODE_H = 54;
const COL_GAP = 40;
const ROW_GAP = 160;
const MIN_GAP = 16;
const PAD = 20;
const CONSUMER_GAP = 86;

export interface PlacedNode extends LineageNode {
  x: number;
  y: number;
  focus: boolean;
}

export interface PlacedEdge {
  from: string;
  to: string;
  /** SVG path data. */
  d: string;
  /** Leaves a node that is late, so the path from a late source is drawn as a warning. */
  late: boolean;
}

export interface LineageLayout {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  width: number;
  height: number;
}

export function layoutLineage(graph: LineageGraph): LineageLayout {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const edges = graph.edges.filter((e) => byId.has(e.from) && byId.has(e.to));
  const inputs = new Map<string, string[]>();
  const outputs = new Map<string, string[]>();
  for (const e of edges) {
    inputs.set(e.to, [...(inputs.get(e.to) ?? []), e.from]);
    outputs.set(e.from, [...(outputs.get(e.from) ?? []), e.to]);
  }

  // Upstream of the focus: walk the inputs back from it.
  const upstream = new Set<string>([graph.focus_id]);
  const stack = [graph.focus_id];
  while (stack.length) {
    const id = stack.pop() as string;
    for (const parent of inputs.get(id) ?? []) {
      if (!upstream.has(parent)) {
        upstream.add(parent);
        stack.push(parent);
      }
    }
  }

  // Column = longest path from a root within the upstream set (cycles are cut by `seen`).
  const column = new Map<string, number>();
  const columnOf = (id: string, seen: Set<string>): number => {
    const known = column.get(id);
    if (known !== undefined) return known;
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = (inputs.get(id) ?? []).filter((p) => upstream.has(p));
    const c = parents.length ? Math.max(...parents.map((p) => columnOf(p, seen) + 1)) : 0;
    seen.delete(id);
    column.set(id, c);
    return c;
  };
  for (const id of upstream) columnOf(id, new Set());
  const focusColumn = column.get(graph.focus_id) ?? 0;

  // Rows: roots in API order, then each column at the mean height of its inputs.
  const y = new Map<string, number>();
  const ordered = graph.nodes.filter((n) => upstream.has(n.id));
  for (let c = 0; c <= focusColumn; c++) {
    const inColumn = ordered.filter((n) => column.get(n.id) === c);
    const wanted = inColumn.map((n, i) => {
      const parents = (inputs.get(n.id) ?? []).filter((p) => y.has(p));
      const target = parents.length
        ? parents.reduce((sum, p) => sum + (y.get(p) as number), 0) / parents.length
        : i * ROW_GAP;
      return { id: n.id, target };
    });
    wanted.sort((a, b) => a.target - b.target);
    let floor = -Infinity;
    for (const w of wanted) {
      const placed = Math.max(w.target, floor);
      y.set(w.id, placed);
      floor = placed + NODE_H + MIN_GAP;
    }
  }

  // Consumers of the focus: alternately above and below it.
  const focusY = y.get(graph.focus_id) ?? 0;
  const consumers = (outputs.get(graph.focus_id) ?? []).filter((id) => !upstream.has(id));
  consumers.forEach((id, i) => {
    const level = Math.floor(i / 2) + 1;
    y.set(id, focusY + (i % 2 === 0 ? -1 : 1) * level * (NODE_H + CONSUMER_GAP));
    column.set(id, focusColumn);
  });

  const placedIds = [...upstream, ...consumers];
  const minY = Math.min(...placedIds.map((id) => y.get(id) as number));
  const nodes: PlacedNode[] = placedIds.map((id) => ({
    ...(byId.get(id) as LineageNode),
    x: PAD + (column.get(id) as number) * (NODE_W + COL_GAP),
    y: PAD + (y.get(id) as number) - minY,
    focus: id === graph.focus_id,
  }));
  const at = new Map(nodes.map((n) => [n.id, n]));

  const placedEdges: PlacedEdge[] = edges
    .filter((e) => at.has(e.from) && at.has(e.to))
    .map((e) => {
      const a = at.get(e.from) as PlacedNode;
      const b = at.get(e.to) as PlacedNode;
      let d: string;
      if (a.x === b.x) {
        // Same column: a consumer above or below the focus.
        const down = b.y > a.y;
        const x = a.x + NODE_W / 2;
        d = `M ${x} ${down ? a.y + NODE_H : a.y} V ${down ? b.y : b.y + NODE_H}`;
      } else {
        const y1 = a.y + NODE_H / 2;
        const y2 = b.y + NODE_H / 2;
        const x1 = a.x + NODE_W;
        const bend = b.x - COL_GAP / 2;
        d = y1 === y2 ? `M ${x1} ${y1} H ${b.x}` : `M ${x1} ${y1} H ${bend} V ${y2} H ${b.x}`;
      }
      return { from: e.from, to: e.to, d, late: a.late };
    });

  return {
    nodes,
    edges: placedEdges,
    width: Math.max(...nodes.map((n) => n.x + NODE_W)) + PAD,
    height: Math.max(...nodes.map((n) => n.y + NODE_H)) + PAD,
  };
}

const escapeXml = (s: string) => s.replace(/[<>&"']/g, (c) => `&#${c.charCodeAt(0)};`);

/**
 * The same layout as a standalone SVG file for "Xuất sơ đồ". Colours are passed in already
 * resolved from the theme tokens, since a file opened elsewhere has no CSS variables.
 */
export function lineageToSvg(
  layout: LineageLayout,
  colors: {
    card: string;
    border: string;
    foreground: string;
    muted: string;
    warning: string;
    background: string;
  },
): string {
  const edges = layout.edges
    .map(
      (e) =>
        `<path d="${e.d}" fill="none" stroke="${e.late ? colors.warning : colors.border}" stroke-width="1.5"/>`,
    )
    .join('');
  const nodes = layout.nodes
    .map((n) => {
      const stroke = n.focus ? colors.foreground : n.late ? colors.warning : colors.border;
      return (
        `<g transform="translate(${n.x} ${n.y})">` +
        `<rect width="${NODE_W}" height="${NODE_H}" rx="6" fill="${colors.card}" stroke="${stroke}" stroke-width="${n.focus ? 1.5 : 1}"/>` +
        `<text x="12" y="23" font-family="Inter, sans-serif" font-size="13" font-weight="500" fill="${colors.foreground}">${escapeXml(n.label)}</text>` +
        `<text x="12" y="41" font-family="Inter, sans-serif" font-size="12" fill="${n.late ? colors.warning : colors.muted}">${escapeXml(n.detail_text)}</text>` +
        `</g>`
      );
    })
    .join('');
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${layout.width}" height="${layout.height}" viewBox="0 0 ${layout.width} ${layout.height}">` +
    `<rect width="100%" height="100%" fill="${colors.background}"/>${edges}${nodes}</svg>`
  );
}
