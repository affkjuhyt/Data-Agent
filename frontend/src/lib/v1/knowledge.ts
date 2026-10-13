import { currentDataSpace, pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * The knowledge layer of the context service (A2 "/v1/knowledge*", UC-07-10…12): compiled pages
 * about a metric, an entity, a source or a topic, each a chain of certified versions whose claims
 * cite sources and whose figures are filled by the system at read time under the reader's
 * permissions (never written by the model, BR-162). Fields marked PROPOSAL are what the screens
 * need in a form A3 does not fix yet. The api's shapes are read into the screens' below; without
 * the PROPOSAL fields the screens get what the api's rows say.
 */

type Schemas = components['schemas'];
export type KnowledgeKind = 'metric' | 'entity' | 'source' | 'topic';
export type KnowledgePageStatus =
  | 'draft'
  | 'pending_review'
  | 'approved'
  | 'certified'
  | 'decertified'
  | 'needs_review'
  | 'retired';

export interface KnowledgePageSummary {
  id: string;
  title: string;
  kind: KnowledgeKind;
  status: KnowledgePageStatus;
}

/** One run of the rendered summary: plain text, or a figure the system filled from a query. */
export type SummarySegment =
  | { text: string }
  | { figure: string; query_run_id: string; source_title: string };

export interface KnowledgeClaim {
  text: string;
  source_title: string;
  source_detail: string;
  valid_text: string;
  status: 'valid' | 'expired';
}

/** GET /v1/knowledge-pages/{id} — RenderedKnowledgePage. */
export interface KnowledgePage extends KnowledgePageSummary {
  version: number;
  certified_at: string | null;
  /** PROPOSAL: the certifier's name for the line under the title. */
  certified_by_name: string | null;
  /** PROPOSAL: a page citing an external source carries the "Nguồn ngoài" badge. */
  external_source: boolean;
  summary: SummarySegment[];
  claims: KnowledgeClaim[];
  /** PROPOSAL: the page's access policy in words. */
  policy: { data_spaces_text: string; classification: string; viewer_rule: string };
  links: { direction: 'out' | 'in'; title: string; page_id: string | null }[];
  version_count: number;
}

export interface KnowledgePageVersion {
  id: string;
  version: number;
  status: 'draft' | 'pending_review' | 'certified' | 'superseded';
  change_note: string | null;
  created_at: string;
}

const readSummary = (raw: Schemas['KnowledgePageView']): KnowledgePageSummary => ({
  id: raw.id,
  title: raw.title,
  kind: raw.kind as KnowledgeKind,
  status: raw.status as KnowledgePageStatus,
});

export const listKnowledgePages = async () => {
  const page = await v1Get<Page<Schemas['KnowledgePageView']>>('/knowledge-pages', {
    page_size: 500,
  });
  return { ...page, items: page.items.map(readSummary) };
};

/** PROPOSAL: the page as the screen draws it, which the mock sends alongside the api's fields. */
export type RenderedWire = Schemas['RenderedKnowledgePage'] & {
  display?: Pick<KnowledgePage, 'summary' | 'claims' | 'policy' | 'links' | 'certified_by_name'>;
};

const CLASSIFICATION: Record<string, string> = {
  public: 'Công khai',
  internal: 'Nội bộ',
  confidential: 'Mật',
  restricted: 'Hạn chế',
};

/** "2026-10-07" → "07/10/2026" */
const day = (iso: string) => iso.slice(0, 10).split('-').reverse().join('/');

/**
 * A claim cites one of the page's sources (source_link_id) and holds from valid_from to
 * valid_to; past valid_to it is expired. Link titles come from the pages the viewer can list.
 */
function readRendered(raw: RenderedWire, titles: Map<string, string>): KnowledgePage {
  const page = raw.page;
  const today = new Date().toISOString().slice(0, 10);
  const derived: NonNullable<RenderedWire['display']> = {
    summary: [{ text: raw.version?.summary || page.index_summary }],
    claims: (raw.claims ?? []).map((c) => {
      const source = (raw.sources ?? []).find((s) => s.id === c.source_link_id);
      return {
        text: c.statement,
        source_title: source?.source_label ?? '',
        source_detail: source?.source_kind ?? '',
        valid_text: c.valid_to
          ? `${day(c.valid_from)} – ${day(c.valid_to)}`
          : `Từ ${day(c.valid_from)}`,
        status: c.valid_to && c.valid_to < today ? 'expired' : 'valid',
      };
    }),
    policy: {
      data_spaces_text: `${page.required_data_space_ids.length} Data Space`,
      classification: CLASSIFICATION[page.classification] ?? page.classification,
      viewer_rule: 'Cần quyền trên mọi nguồn của trang',
    },
    links: (raw.links ?? []).map((l) => ({
      direction: 'out' as const,
      title: titles.get(l.to_page_id) ?? 'Trang liên kết',
      page_id: l.to_page_id,
    })),
    certified_by_name: null,
  };
  return {
    ...readSummary(page),
    ...(raw.display ?? derived),
    version: raw.version?.version ?? page.latest_version_no,
    certified_at: raw.version?.certified_at ?? null,
    external_source: page.has_external_source,
    version_count: page.latest_version_no,
  };
}

export const getKnowledgePage = async (id: string) => {
  const [raw, pages] = await Promise.all([
    v1Get<RenderedWire>(`/knowledge-pages/${pathId(id)}`),
    listKnowledgePages().catch(() => null),
  ]);
  return readRendered(raw, new Map<string, string>(pages?.items.map((p) => [p.id, p.title]) ?? []));
};

export const listKnowledgePageVersions = async (id: string) => {
  const page = await v1Get<Page<Schemas['KnowledgeVersionView']>>(
    `/knowledge-pages/${pathId(id)}/versions`,
  );
  return {
    ...page,
    items: page.items.map(
      (v): KnowledgePageVersion => ({
        id: v.id,
        version: v.version,
        status: v.status as KnowledgePageVersion['status'],
        change_note: v.change_note,
        created_at: v.created_at,
      }),
    ),
  };
};

/** KnowledgeRunCreate needs the Data Space; the one chosen in the top bar is sent. */
const spaceForRun = () => {
  const space = currentDataSpace();
  if (!space) throw new V1Error(400, 'data_space_required', 'Chọn một Data Space trước.');
  return space;
};

/** A2: POST /v1/knowledge-runs (202) — compile one page again now (an ingest run on it). */
export const recompileKnowledgePage = (pageId: string) =>
  v1Send<Schemas['services__api__modules__context__schemas__Accepted']>('POST', '/knowledge-runs', {
    kind: 'ingest',
    data_space_id: spaceForRun(),
    source_kind: 'knowledge_page',
    source_ref: pageId,
  } satisfies Schemas['KnowledgeRunCreate']);

/* ─── Rà soát tri thức (UC-07-11) ─────────────────────────────────────── */

/** The latest lint run, in the screen's terms; what the api's run cannot say is null (PROPOSAL). */
export interface KnowledgeLintRun {
  id: string;
  finished_at: string | null;
  page_count: number;
  open_findings: number;
  stale_pages: number | null;
  skipped_source_pct: number;
  duration_minutes: number | null;
  /** "tối đa 30 phút, 50 thẻ Cần bạn" */
  limits_text: string;
  /** "lượt dùng mô hình chạy chủ nhật 02:00" */
  schedule_text: string;
}

export type FindingKind =
  | 'contradiction'
  | 'expired_claim'
  | 'decertified_source'
  | 'orphan'
  | 'metric_without_page'
  | 'undefined_term'
  | 'stale_condition'
  | 'policy_drift';

/** The api's finding kinds (knowledge_lint_finding.kind) by the screen's names. */
const KIND: Record<string, FindingKind> = {
  orphan_page: 'orphan',
  source_decertified: 'decertified_source',
};

export type FindingAction =
  | 'propose_edit'
  | 'mark_stale'
  | 'retire_page'
  | 'create_page'
  | 'dismiss'
  | 'reassign';

/** GET /v1/knowledge-findings — KnowledgeLintFinding. */
export interface KnowledgeFinding {
  id: string;
  severity: 'high' | 'medium' | 'low';
  kind: FindingKind;
  /** PROPOSAL: the page (or pages, term, metric) it is about, in words. */
  subject_text: string;
  detail: string;
  status: 'open' | 'resolved' | 'dismissed';
  /** PROPOSAL: the two sides of a contradiction, or other evidence lines. */
  evidence: { label: string; value: string }[];
  /** PROPOSAL: the agent's suggested action, in a sentence. */
  suggestion: string | null;
}

export type LintRunWire = Schemas['KnowledgeRunView'] & Partial<KnowledgeLintRun>;

/** Skipped sources are the ones whose hash did not change since the last run. */
function readLintRun(raw: LintRunWire): KnowledgeLintRun {
  const minutes =
    raw.started_at && raw.finished_at
      ? Math.round((Date.parse(raw.finished_at) - Date.parse(raw.started_at)) / 60_000)
      : null;
  return {
    id: raw.id,
    finished_at: raw.finished_at,
    page_count: raw.page_count ?? raw.pages_analyzed,
    open_findings: raw.open_findings ?? raw.findings_count,
    stale_pages: raw.stale_pages ?? null,
    skipped_source_pct:
      raw.skipped_source_pct ??
      (raw.sources_seen > 0
        ? Math.round((raw.sources_skipped_unchanged / raw.sources_seen) * 100)
        : 0),
    duration_minutes: raw.duration_minutes ?? minutes,
    limits_text: raw.limits_text ?? `tối đa ${raw.page_limit} trang`,
    schedule_text: raw.schedule_text ?? '',
  };
}

/** A2: GET /v1/knowledge-runs ?kind ?status — the newest finished lint run first. */
export const getLatestLintRun = async () => {
  const page = await v1Get<Page<LintRunWire>>('/knowledge-runs', {
    kind: 'lint',
    status: 'succeeded',
  });
  return { ...page, items: page.items.map(readLintRun) };
};

export type FindingWire = Schemas['FindingView'] &
  Partial<Pick<KnowledgeFinding, 'subject_text' | 'evidence' | 'suggestion'>>;

/** `detail` is jsonb: a sentence, or an object whose text or message says it. */
const detailText = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (typeof detail === 'object' && detail !== null) {
    const { text, message } = detail as { text?: unknown; message?: unknown };
    if (typeof text === 'string') return text;
    if (typeof message === 'string') return message;
  }
  return '';
};

const readFinding = (raw: FindingWire, titles: Map<string, string>): KnowledgeFinding => ({
  id: raw.id,
  severity: raw.severity as KnowledgeFinding['severity'],
  kind: KIND[raw.kind] ?? (raw.kind as FindingKind),
  subject_text:
    raw.subject_text ??
    [raw.page_id, raw.related_page_id]
      .map((id) => (id ? titles.get(id) : undefined))
      .filter(Boolean)
      .join(' ↔ '),
  detail: detailText(raw.detail),
  status: raw.status as KnowledgeFinding['status'],
  evidence: raw.evidence ?? [],
  suggestion: raw.suggestion ?? null,
});

/** The pages' titles name what each finding is about. */
export const listOpenFindings = async () => {
  const [page, pages] = await Promise.all([
    v1Get<Page<FindingWire>>('/knowledge-findings', { status: 'open', page_size: 500 }),
    listKnowledgePages().catch(() => null),
  ]);
  const titles = new Map<string, string>(pages?.items.map((p) => [p.id, p.title]) ?? []);
  return { ...page, items: page.items.map((f) => readFinding(f, titles)) };
};

/** A2: one of six fixed actions; dismiss needs a reason (BR-166, hidden 90 days). */
export const resolveFinding = async (
  id: string,
  body: { action: FindingAction; reason?: string; assignee_id?: string },
) =>
  readFinding(
    await v1Send<FindingWire>('POST', `/knowledge-findings/${pathId(id)}:resolve`, body),
    new Map<string, string>(),
  );

/** A2: POST /v1/knowledge-runs (202) — a lint run now. */
export const runKnowledgeLint = () =>
  v1Send<Schemas['services__api__modules__context__schemas__Accepted']>('POST', '/knowledge-runs', {
    kind: 'lint',
    data_space_id: spaceForRun(),
  } satisfies Schemas['KnowledgeRunCreate']);

/* ─── Lưu câu trả lời thành tri thức (UC-07-12) ───────────────────────── */

/**
 * What saving an answer starts from: the answer (A2: GET /v1/answers/{id}) and the page of the
 * knowledge index it fits best (A2: GET /v1/knowledge/index?q=). The paragraph and its figure
 * placeholders bound to the query (BR-162) are built by the server when it saves, so the dialog
 * shows the answer, not a preview of the paragraph.
 */
export interface KnowledgeDraft {
  answer_text: string;
  target_page: { id: string; title: string } | null;
}

export async function getKnowledgeDraft(answerId: string): Promise<KnowledgeDraft> {
  const answer = await v1Get<Schemas['AnswerView']>(`/answers/${pathId(answerId)}`);
  // The index is only a suggestion: without it the user picks the page.
  const index = await v1Get<Page<Schemas['KnowledgeIndexEntry']>>('/knowledge/index', {
    q: answer.text.slice(0, 200),
    limit: 1,
  }).catch(() => null);
  const top = index?.items[0];
  return { answer_text: answer.text, target_page: top ? { id: top.id, title: top.title } : null };
}

/**
 * A2: POST /v1/knowledge-pages:from-answer (202) — the answer becomes a draft version of the
 * target page, submitted for review; 422 business_rule_violated when a figure has no query run,
 * a citation failed its check or the answer holds personal data (BR-167). A paragraph is
 * appended to an existing page; a page is a new one.
 */
export const saveAnswerToKnowledge = (body: {
  answer_id: string;
  mode: 'paragraph' | 'page';
  target_page_id?: string;
  title?: string;
  note?: string;
}) =>
  v1Send<Schemas['services__api__modules__context__schemas__Accepted']>(
    'POST',
    '/knowledge-pages:from-answer',
    {
      ...body,
      mode: body.mode === 'paragraph' ? 'append' : 'new_page',
    } satisfies Schemas['FromAnswer'],
  );
