import { v1Get, v1Send, type Page } from './client';

/*
 * conflict.source_authority, for "P07 · Thẩm quyền nguồn" (2302:19178, UC-07-05): per kind of
 * data, which source wins when two disagree and from what gap the agent must ask instead.
 * A2: GET /v1/source-authorities, PUT /v1/source-authorities {data_kind, ranking, materiality}
 * — a draft, backtested on 90 days of conflicts, in force after approval; 422
 * business_rule_violated when a manual or external source is ranked above a level-1 one (BR-121).
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

export const listSourceAuthorities = () =>
  v1Get<Page<SourceAuthority>>('/source-authorities', { page_size: 500 });

export const saveSourceAuthority = (body: {
  data_kind: string;
  ranking: string[];
  materiality: string | null;
}) => v1Send<SourceAuthority>('PUT', '/source-authorities', body);
