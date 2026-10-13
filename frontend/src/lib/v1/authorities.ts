import { listSources } from './api';
import { v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * conflict.source_authority, for "P07 · Thẩm quyền nguồn" (2302:19178, UC-07-05): per kind of
 * data, which source wins when two disagree and from what gap the agent must ask instead.
 * A2: GET /v1/source-authorities, PUT /v1/source-authorities {data_kind, ranking, materiality}
 * — a draft, backtested on 90 days of conflicts, in force after approval; 422
 * business_rule_violated when a manual or external source is ranked above a level-1 one (BR-121).
 * The api ranks source ids; their names and levels come from GET /v1/sources.
 */
export interface RankedSource {
  source_id: string;
  name: string;
  authority_level: 'level_1' | 'level_2' | 'level_3' | 'reference';
  /** PROPOSAL: manual uploads and external files, which may not outrank a level-1 source. */
  manual: boolean;
}

export interface SourceAuthority {
  id: string;
  data_kind: string;
  /** PROPOSAL: "Dư nợ, nhóm nợ" */
  data_kind_label: string;
  ranking: RankedSource[];
  /** PROPOSAL: the threshold in words, "0,1% hoặc 1 tỷ"; null when it does not apply. */
  materiality: string | null;
  /** PROPOSAL: who decides when the gap is material. */
  decider_name: string;
  /** draft = saved, waiting for approval after its backtest. */
  status: 'active' | 'draft';
  /** PROPOSAL: the latest conflict this rule decided, in one paragraph. */
  last_conflict_text: string | null;
}

type AuthorityWire = components['schemas']['SourceAuthorityView'] & {
  /** PROPOSAL: the screen's row, which the mock sends under `display`. */
  display?: SourceAuthority;
};
type Materiality = components['schemas']['Materiality'];

const pct = (fraction: number) => `${String(+(fraction * 100).toFixed(4)).replace('.', ',')}%`;

/** BR-120 thresholds in words: relative for amounts, absolute for counts. */
export function materialityText(raw: unknown): string | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const { amount_pct, count } = raw as { amount_pct?: number | string; count?: number };
  const parts = [
    amount_pct !== undefined && pct(Number(amount_pct)),
    count ? `${count} khoản` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' hoặc ') : null;
}

/**
 * The threshold as typed ("0,1% hoặc 5 khoản") back into Materiality: the percentage is a
 * fraction of the amount, a number of khoản/dòng/bản ghi is the count. Nothing typed sends none.
 */
export function parseMateriality(text: string | null): Materiality | null {
  if (!text?.trim()) return null;
  const share = /(\d+(?:[.,]\d+)?)\s*%/.exec(text);
  const count = /(\d+)\s*(?:khoản|dòng|bản ghi)/.exec(text);
  return {
    amount_pct: share ? Number(share[1].replace(',', '.')) / 100 : 0.0005,
    count: count ? Number(count[1]) : 0,
  };
}

const MANUAL_KINDS = ['file', 'sharepoint', 'sftp'];

function readAuthority(
  raw: AuthorityWire,
  sources: Map<string, { name: string; level: string; kind: string }>,
): SourceAuthority {
  if (raw.display) return raw.display;
  return {
    id: raw.id,
    data_kind: raw.data_kind,
    data_kind_label: raw.data_kind,
    ranking: raw.ranking.map((id) => {
      const s = sources.get(id);
      return {
        source_id: id,
        name: s?.name ?? id,
        authority_level: (s?.level ?? 'level_2') as RankedSource['authority_level'],
        manual: MANUAL_KINDS.includes(s?.kind ?? ''),
      };
    }),
    materiality: materialityText(raw.materiality),
    decider_name: '',
    // superseded rows are history; the screen shows what is in force or waits for approval.
    status: raw.status === 'active' ? 'active' : 'draft',
    last_conflict_text: null,
  };
}

export const listSourceAuthorities = async (): Promise<Page<SourceAuthority>> => {
  const [page, sources] = await Promise.all([
    v1Get<Page<AuthorityWire>>('/source-authorities', { page_size: 500 }),
    listSources().catch(() => null),
  ]);
  const byId = new Map<string, { name: string; level: string; kind: string }>(
    sources?.items.map((s) => [s.id, { name: s.name, level: s.authority_level, kind: s.kind }]) ??
      [],
  );
  return {
    ...page,
    items: page.items.filter((a) => a.status !== 'superseded').map((a) => readAuthority(a, byId)),
  };
};

export const saveSourceAuthority = (body: {
  data_kind: string;
  ranking: string[];
  materiality: string | null;
}) =>
  v1Send<AuthorityWire>('PUT', '/source-authorities', {
    data_kind: body.data_kind,
    ranking: body.ranking,
    materiality: parseMateriality(body.materiality),
  } satisfies components['schemas']['SourceAuthorityPut']);
