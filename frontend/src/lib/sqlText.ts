import type { ChartSpec } from '../features/ask/types';

/**
 * The SQL workbench has no server formatter and no compiler endpoint that says "this statement
 * is a certified metric". These helpers stay deterministic: highlight and indent the text the
 * analyst typed, draw a chart only when the grid itself has a category and a number, and name
 * a metric only when its code appears as an identifier.
 */

const KEYWORDS = new Set([
  'select',
  'from',
  'where',
  'group',
  'by',
  'order',
  'having',
  'limit',
  'offset',
  'join',
  'left',
  'right',
  'inner',
  'full',
  'cross',
  'outer',
  'on',
  'and',
  'or',
  'not',
  'as',
  'in',
  'is',
  'null',
  'like',
  'between',
  'distinct',
  'union',
  'all',
  'case',
  'when',
  'then',
  'else',
  'end',
  'with',
  'asc',
  'desc',
  'exists',
  'cast',
  'over',
  'partition',
]);

const CLAUSE = new Set([
  'select',
  'from',
  'where',
  'group by',
  'order by',
  'having',
  'limit',
  'union',
  'union all',
  'join',
  'left join',
  'right join',
  'inner join',
  'full join',
  'cross join',
]);

export type SqlTokenKind = 'keyword' | 'string' | 'number' | 'comment' | 'plain';

export interface SqlToken {
  text: string;
  kind: SqlTokenKind;
}

/** One highlighted line. A newline is a token boundary, never a painted character. */
export function sqlLines(sql: string): SqlToken[][] {
  const lines: SqlToken[][] = [[]];
  for (const token of sqlTokens(sql)) {
    const parts = token.text.split('\n');
    parts.forEach((part, index) => {
      if (index > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, kind: token.kind });
    });
  }
  return lines;
}

/** Split SQL without dropping characters, so the highlight lines up with the textarea. */
export function sqlTokens(sql: string): SqlToken[] {
  const tokens: SqlToken[] = [];
  let i = 0;
  while (i < sql.length) {
    const rest = sql.slice(i);
    if (rest.startsWith('--')) {
      const end = rest.indexOf('\n');
      const text = end === -1 ? rest : rest.slice(0, end);
      tokens.push({ text, kind: 'comment' });
      i += text.length;
      continue;
    }
    if (rest.startsWith('/*')) {
      const end = rest.indexOf('*/');
      const text = end === -1 ? rest : rest.slice(0, end + 2);
      tokens.push({ text, kind: 'comment' });
      i += text.length;
      continue;
    }
    const quote = rest[0];
    if (quote === "'" || quote === '"') {
      let j = 1;
      while (j < rest.length) {
        if (rest[j] === quote && rest[j + 1] === quote) {
          j += 2;
          continue;
        }
        if (rest[j] === quote) {
          j += 1;
          break;
        }
        j += 1;
      }
      tokens.push({ text: rest.slice(0, j), kind: quote === "'" ? 'string' : 'plain' });
      i += j;
      continue;
    }
    const word = rest.match(/^[A-Za-z_][\w$]*/);
    if (word) {
      const text = word[0];
      tokens.push({ text, kind: KEYWORDS.has(text.toLowerCase()) ? 'keyword' : 'plain' });
      i += text.length;
      continue;
    }
    const number = rest.match(/^\d+(?:\.\d+)?/);
    if (number) {
      tokens.push({ text: number[0], kind: 'number' });
      i += number[0].length;
      continue;
    }
    tokens.push({ text: rest[0], kind: 'plain' });
    i += 1;
  }
  return tokens;
}

/** Indent each clause onto its own line. Strings and comments stay intact. */
export function formatReadOnlySql(sql: string): string {
  const tokens = sqlTokens(sql.trim());
  let out = '';
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    const next = tokens[i + 1];
    const pair =
      token.kind === 'keyword' && next?.kind === 'keyword'
        ? `${token.text} ${next.text}`.toLowerCase()
        : '';
    const clause = CLAUSE.has(pair)
      ? pair
      : token.kind === 'keyword' && CLAUSE.has(token.text.toLowerCase())
        ? token.text.toLowerCase()
        : '';
    if (clause && out.trim() !== '') out = out.replace(/\s*$/, '') + '\n';
    if (clause && pair) {
      out += `${token.text.toUpperCase()} ${next.text.toUpperCase()}`;
      i += 1;
      continue;
    }
    if (clause) {
      out += token.text.toUpperCase();
      continue;
    }
    if (token.kind === 'keyword') {
      out += token.text.toUpperCase();
      continue;
    }
    out += token.text;
  }
  return out
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export interface SqlBrowseTable {
  id: string;
  schema: string;
  name: string;
  qualified: string;
  description: string | null;
  sourceId: string | null;
  sourceName: string | null;
}

export interface SqlBrowseSchema {
  name: string;
  tables: SqlBrowseTable[];
}

export interface SqlBrowseSource {
  id: string;
  name: string;
  schemas: SqlBrowseSchema[];
}

/** Schema browser: source, then schema, then table. Same names sort together. */
export function sqlBrowseTree(tables: SqlBrowseTable[]): SqlBrowseSource[] {
  const sources = new Map<
    string,
    { id: string; name: string; schemas: Map<string, SqlBrowseTable[]> }
  >();
  for (const table of tables) {
    const id = table.sourceId ?? 'derived';
    const source = sources.get(id) ?? {
      id,
      name: table.sourceName ?? (table.sourceId ? 'Nguồn dữ liệu' : 'Bảng dẫn xuất'),
      schemas: new Map<string, SqlBrowseTable[]>(),
    };
    const schema = table.schema || '(mặc định)';
    const list = source.schemas.get(schema) ?? [];
    list.push(table);
    source.schemas.set(schema, list);
    sources.set(id, source);
  }
  return [...sources.values()]
    .map((source) => ({
      id: source.id,
      name: source.name,
      schemas: [...source.schemas.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([name, list]) => ({
          name,
          tables: [...list].sort((a, b) => a.name.localeCompare(b.name)),
        })),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export interface SqlSchemaTable {
  name: string;
  columns: { name: string }[];
}

/**
 * Nested schema for an SQL editor: `mart.loans` becomes `{ mart: { loans: [columns] } }`.
 * A name without a dot stays a top-level table. A unique table name is also hoisted to
 * the top level, because the editor only offers top-level names while the caret is not
 * after a schema dot — otherwise typing after FROM never lists the tables.
 */
export function sqlSchemaTree(tables: SqlSchemaTable[]): Record<string, unknown> {
  const root: Record<string, unknown> = {};
  const bare = new Map<string, { columns: string[]; count: number }>();
  for (const table of tables) {
    const columns = table.columns.map((column) => column.name);
    const dot = table.name.indexOf('.');
    if (dot <= 0 || dot === table.name.length - 1) {
      root[table.name] = columns;
      continue;
    }
    const schema = table.name.slice(0, dot);
    const name = table.name.slice(dot + 1);
    const existing = root[schema];
    const bucket =
      existing && !Array.isArray(existing) ? (existing as Record<string, string[]>) : {};
    bucket[name] = columns;
    root[schema] = bucket;
    const previous = bare.get(name);
    bare.set(name, { columns, count: (previous?.count ?? 0) + 1 });
  }
  for (const [name, entry] of bare) {
    if (entry.count !== 1) continue;
    const occupied = root[name];
    if (occupied && !Array.isArray(occupied)) continue;
    if (!occupied) root[name] = entry.columns;
  }
  return root;
}

/** Schema and table named by the first FROM, so completion can offer that table's columns. */
export function sqlCompletionContext(sql: string): { schema?: string; table?: string } {
  const match = sql.match(/\bfrom\s+([a-zA-Z_][\w$]*)(?:\.([a-zA-Z_][\w$]*))?/i);
  if (!match) return {};
  if (match[2]) return { schema: match[1], table: match[2] };
  return { table: match[1] };
}

function schemaNode(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Defaults for the editor. A half-typed identifier (`FROM u`) must not become the
 * default table: the completion engine then invents a child named `u` and hides
 * the real tables.
 */
export function sqlCompletionDefaults(
  schema: Record<string, unknown>,
  context: { schema?: string; table?: string },
): { defaultSchema?: string; defaultTable?: string } {
  const node = context.schema ? schema[context.schema] : undefined;
  const schemaKnown = schemaNode(node);
  const tableKnown = context.table
    ? (schemaKnown && context.table in node) || Array.isArray(schema[context.table])
    : false;
  const result: { defaultSchema?: string; defaultTable?: string } = {};
  if (schemaKnown && context.schema) result.defaultSchema = context.schema;
  if (tableKnown && context.table) result.defaultTable = context.table;
  return result;
}

interface PositionedToken {
  token: SqlToken;
  from: number;
  to: number;
}

const positionedTokens = (sql: string): PositionedToken[] => {
  const tokens: PositionedToken[] = [];
  let offset = 0;
  for (const token of sqlTokens(sql)) {
    const to = offset + token.text.length;
    tokens.push({ token, from: offset, to });
    offset = to;
  }
  return tokens;
};

const isGap = (token: SqlToken) => token.kind === 'comment' || /^\s+$/.test(token.text);

const isKeyword = (placed: PositionedToken | undefined, word: string) =>
  placed?.token.kind === 'keyword' && placed.token.text.toLowerCase() === word;

const skipGap = (tokens: PositionedToken[], index: number) => {
  let cursor = index;
  while (tokens[cursor] && isGap(tokens[cursor].token)) cursor += 1;
  return cursor;
};

/** An identifier, optionally quoted. `next` is the token index after it. */
const readIdent = (
  tokens: PositionedToken[],
  index: number,
): { name: string; from: number; to: number; next: number } | null => {
  const placed = tokens[index];
  if (!placed) return null;
  if (placed.token.text === '"') {
    let cursor = index + 1;
    let name = '';
    while (tokens[cursor] && tokens[cursor].token.text !== '"') {
      name += tokens[cursor].token.text;
      cursor += 1;
    }
    const end = tokens[cursor]?.to ?? tokens[cursor - 1]?.to ?? placed.to;
    return { name, from: placed.from, to: end, next: tokens[cursor] ? cursor + 1 : cursor };
  }
  if (!/^[A-Za-z_][\w$]*$/.test(placed.token.text)) return null;
  return { name: placed.token.text, from: placed.from, to: placed.to, next: index + 1 };
};

const skipParens = (tokens: PositionedToken[], open: number) => {
  let depth = 0;
  for (let cursor = open; cursor < tokens.length; cursor += 1) {
    if (tokens[cursor].token.text === '(') depth += 1;
    if (tokens[cursor].token.text === ')') {
      depth -= 1;
      if (depth === 0) return cursor + 1;
    }
  }
  return tokens.length;
};

const cteNames = (tokens: PositionedToken[]) => {
  const names = new Set<string>();
  for (let i = 0; i < tokens.length; i += 1) {
    if (!isKeyword(tokens[i], 'with')) continue;
    let cursor = i + 1;
    for (;;) {
      cursor = skipGap(tokens, cursor);
      const ident = readIdent(tokens, cursor);
      if (!ident) break;
      const after = skipGap(tokens, ident.next);
      if (!isKeyword(tokens[after], 'as')) break;
      names.add(ident.name.toLowerCase());
      cursor = skipGap(tokens, after + 1);
      if (tokens[cursor]?.token.text !== '(') break;
      cursor = skipGap(tokens, skipParens(tokens, cursor));
      if (tokens[cursor]?.token.text !== ',') break;
      cursor += 1;
    }
  }
  return names;
};

const relationKnown = (typed: string, known: string[]) =>
  known.some((name) => {
    if (name.startsWith(typed)) return true;
    const table = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1) : name;
    return table.startsWith(typed);
  });

/**
 * Tables named after FROM or JOIN that match neither the catalog nor a CTE.
 * A prefix of a real name is left alone, so a half-typed identifier is not an error.
 */
export function unknownSqlRelations(
  sql: string,
  tables: { name: string }[],
): { from: number; to: number; name: string }[] {
  const known = tables.map((table) => table.name.toLowerCase());
  const tokens = positionedTokens(sql);
  const ctes = cteNames(tokens);
  const problems: { from: number; to: number; name: string }[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    if (!isKeyword(tokens[i], 'from') && !isKeyword(tokens[i], 'join')) continue;
    let cursor = skipGap(tokens, i + 1);
    if (tokens[cursor]?.token.text === '(') continue;
    const first = readIdent(tokens, cursor);
    if (!first) continue;
    let name = first.name;
    let to = first.to;
    cursor = skipGap(tokens, first.next);
    if (tokens[cursor]?.token.text === '.') {
      const second = readIdent(tokens, skipGap(tokens, cursor + 1));
      if (second) {
        name = `${name}.${second.name}`;
        to = second.to;
      }
    }
    const folded = name.toLowerCase();
    if (ctes.has(folded) || relationKnown(folded, known)) continue;
    problems.push({ from: first.from, to, name });
  }
  return problems;
}

/**
 * The statement containing `caret`. Semicolons inside strings and comments do not split.
 * A caret that has not moved (0) is the first statement, which is the whole script when
 * there is only one.
 */
export function statementAround(sql: string, caret: number): string {
  const spans: { start: number; end: number }[] = [];
  let start = 0;
  let offset = 0;
  for (const token of sqlTokens(sql)) {
    const tokenEnd = offset + token.text.length;
    if (token.kind === 'plain' && token.text === ';') {
      spans.push({ start, end: tokenEnd });
      start = tokenEnd;
    }
    offset = tokenEnd;
  }
  if (start < sql.length || spans.length === 0) spans.push({ start, end: sql.length });
  const meaningful = spans.filter((span) => sql.slice(span.start, span.end).trim() !== '');
  if (meaningful.length === 0) return sql.trim();
  const at = Math.max(0, Math.min(caret, sql.length));
  const hit =
    meaningful.find((span) => at >= span.start && at <= span.end) ??
    meaningful[meaningful.length - 1];
  return sql.slice(hit.start, hit.end).trim();
}

/** A server message that names a line ("line 3", "dòng 3"). */
export function sqlErrorLine(message: string): number | null {
  const match = message.match(/(?:line|dòng)\s+(\d+)/i);
  if (!match) return null;
  const line = Number(match[1]);
  return Number.isInteger(line) && line > 0 ? line : null;
}

export interface SqlMetricRef {
  id: string;
  code: string;
  version: number | null;
}

/** The first certified metric whose code is an identifier in the statement. */
export function mentionedMetric(sql: string, metrics: SqlMetricRef[]): SqlMetricRef | null {
  const ordered = [...metrics].sort((a, b) => b.code.length - a.code.length);
  for (const metric of ordered) {
    if (!metric.code) continue;
    const escaped = metric.code.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (new RegExp(`(?<![\\w$])${escaped}(?![\\w$])`, 'i').test(sql)) return metric;
  }
  return null;
}

/** Vietnamese "0,0284" and grouped "1.234,5", plus a plain number. Masked text is not a number. */
export function sqlNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || text.includes('*')) return null;
  const normalized = text.includes(',') ? text.replace(/\./g, '').replace(',', '.') : text;
  if (!/^-?\d+(\.\d+)?$/.test(normalized)) return null;
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

export function columnKinds(
  columns: string[],
  rows: (string | number | null)[][],
): { text: string[]; numeric: string[] } {
  const text: string[] = [];
  const numeric: string[] = [];
  columns.forEach((name, index) => {
    const values = rows.map((row) => row[index]).filter((cell) => cell !== null && cell !== '');
    if (values.length === 0) return;
    if (values.every((cell) => sqlNumber(cell) !== null)) numeric.push(name);
    else text.push(name);
  });
  return { text, numeric };
}

/** A bar of the first category against one measure. Null when the grid has no such pair. */
export function chartFromGrid(
  columns: string[],
  rows: (string | number | null)[][],
  x: string,
  y: string,
): ChartSpec | null {
  const xi = columns.indexOf(x);
  const yi = columns.indexOf(y);
  if (xi < 0 || yi < 0) return null;
  const data = rows
    .map((row) => {
      const value = sqlNumber(row[yi]);
      if (value === null) return null;
      return { [x]: String(row[xi] ?? ''), [y]: value };
    })
    .filter((point): point is Record<string, string | number> => point !== null)
    .slice(0, 24);
  if (data.length === 0) return null;
  return {
    chart_type: 'bar',
    title: `${y} theo ${x}`,
    x_key: x,
    y_keys: [y],
    data,
  };
}
