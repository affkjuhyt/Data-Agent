/**
 * The context layer as the api sends it, without the PROPOSAL display fields: the readers derive
 * what the screens draw from the api's rows (Lớp ngữ cảnh, A2), and a metric is put together from
 * GET /v1/metrics/{id} and GET /v1/lineage.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getAnswerProduct } from './answerProducts';
import { getKnowledgePage, getLatestLintRun, listOpenFindings } from './knowledge';
import { listCatalogMetrics, readMetricProfile } from './metricCatalog';
import type { components } from './schema';
import { listGlossary, listUnknownPhrases } from './semanticLayer';

const at = '2026-10-01T09:00:00Z';
const page = (items: unknown[]) => HttpResponse.json({ items, next_page_token: '' });
const kpage = {
  id: 'kp-1',
  title: 'Ngân hàng B',
  kind: 'entity',
  status: 'certified',
  index_summary: 'Tóm tắt chỉ mục',
  classification: 'internal',
  has_external_source: true,
  latest_version_no: 3,
  required_data_space_ids: ['ds-1', 'ds-2'],
};

const server = setupServer(
  http.get('*/v1/metrics', () =>
    page([{ id: 'm-1', code: 'NPL', display_name: null, certified: true, current_version: 4 }]),
  ),
  http.get('*/v1/terms', () =>
    page([
      {
        id: 't-1',
        term: 'Nợ xấu',
        definition: 'NPL_RATIO',
        status: 'active',
        meanings: [
          { label: 'Nhóm 3–5', expression: 'debt_group >= 3' },
          { label: 'Nhóm 2–5', expression: 'debt_group >= 2' },
        ],
        default_meaning: 0,
      },
      { id: 't-2', term: 'CASA', definition: 'CASA_RATIO', status: 'pending_review', meanings: [] },
    ]),
  ),
  http.get('*/v1/unmapped-phrases', () =>
    page([
      {
        id: 'u-1',
        phrase_norm: 'khách vip',
        ask_count: 9,
        suggested_target: { code: 'SEG_PRIORITY' },
      },
    ]),
  ),
  http.get('*/v1/knowledge-pages', () =>
    page([kpage, { ...kpage, id: 'kp-2', title: 'Ngân hàng K' }]),
  ),
  http.get('*/v1/knowledge-pages/:id', () =>
    HttpResponse.json({
      page: kpage,
      version: { version: 3, summary: 'B công bố lãi suất hằng tuần.', certified_at: at },
      claims: [
        {
          statement: 'Phí chuyển khoản online miễn phí',
          source_link_id: 's-1',
          valid_from: '2026-09-01',
          valid_to: null,
        },
        {
          statement: 'Sáp nhập công ty con',
          source_link_id: 's-1',
          valid_from: '2025-03-12',
          valid_to: '2025-12-31',
        },
      ],
      sources: [
        { id: 's-1', source_label: 'Biểu phí 07/10/2026', source_kind: 'document_version' },
      ],
      links: [{ id: 'l-1', link_kind: 'related', to_page_id: 'kp-2' }],
      figures: [],
      labels: [],
    }),
  ),
  http.get('*/v1/knowledge-runs', () =>
    page([
      {
        id: 'kr-1',
        started_at: '2026-10-10T01:00:00Z',
        finished_at: '2026-10-10T01:12:00Z',
        pages_analyzed: 400,
        findings_count: 7,
        sources_seen: 200,
        sources_skipped_unchanged: 120,
        page_limit: 50,
      },
    ]),
  ),
  http.get('*/v1/knowledge-findings', () =>
    page([
      {
        id: 'f-1',
        kind: 'source_decertified',
        severity: 'high',
        status: 'open',
        detail: { text: 'Dataset v41 bị thu hồi' },
        page_id: 'kp-1',
        related_page_id: 'kp-2',
      },
    ]),
  ),
  http.get('*/v1/data-products/:id', () =>
    HttpResponse.json({
      id: 'dp-1',
      code: 'FUEL',
      name: 'Giá xăng',
      status: 'draft',
      version: 1,
      description: 'Giá bán lẻ',
      freshness_statement: 'Thứ Năm 16:30',
      freshness_state: 'fresh',
      out_of_scope_note: null,
      sample_questions: [{ text: 'Giá RON95 tuần này?' }],
      scope_entities: [{ dimension: 'vùng', values: ['1', '2'] }],
      members: [
        {
          id: 'mem-1',
          member_kind: 'dataset',
          member_ref: 'gia_xang',
          role: 'primary',
          classification_confirmed_by: 'u-1',
        },
      ],
      open_error_book_entries: 2,
      latest_eval_run: { passed: 9, item_count: 10 },
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('the context layer from the api', () => {
  it('reads the metric catalog from MetricView', async () => {
    const [m] = (await listCatalogMetrics()).items;
    expect(m).toMatchObject({ name: 'NPL', status: 'certified', version: 4, usage_text: '' });
  });

  it('reads a term with two meanings as ambiguous and a pending one as a draft', async () => {
    const [npl, casa] = (await listGlossary()).items;
    expect(npl.status).toBe('ambiguous');
    expect(npl.meanings).toEqual([
      { label: 'Nhóm 3–5', expression: 'debt_group >= 3', default: true },
      { label: 'Nhóm 2–5', expression: 'debt_group >= 2', default: false },
    ]);
    expect(casa).toMatchObject({ status: 'draft', maps_to: 'CASA_RATIO' });
  });

  it('reads the unknown phrases from /unmapped-phrases', async () => {
    expect((await listUnknownPhrases()).items).toEqual([
      {
        id: 'u-1',
        phrase: 'khách vip',
        count: 9,
        suggestion: 'Đề xuất: SEG_PRIORITY',
        draft: { term: 'khách vip', definition: '' },
      },
    ]);
  });

  it('renders a knowledge page from its claims, sources and links', async () => {
    const p = await getKnowledgePage('kp-1');
    expect(p.summary).toEqual([{ text: 'B công bố lãi suất hằng tuần.' }]);
    expect(p.claims.map((c) => [c.text, c.source_title, c.valid_text, c.status])).toEqual([
      ['Phí chuyển khoản online miễn phí', 'Biểu phí 07/10/2026', 'Từ 01/09/2026', 'valid'],
      ['Sáp nhập công ty con', 'Biểu phí 07/10/2026', '12/03/2025 – 31/12/2025', 'expired'],
    ]);
    expect(p.links).toEqual([{ direction: 'out', title: 'Ngân hàng K', page_id: 'kp-2' }]);
    expect(p.policy.data_spaces_text).toBe('2 Data Space');
    expect(p).toMatchObject({ version: 3, version_count: 3, external_source: true });
  });

  it('derives the lint run figures and names a finding by its pages', async () => {
    const [run] = (await getLatestLintRun()).items;
    expect(run).toMatchObject({
      page_count: 400,
      open_findings: 7,
      skipped_source_pct: 60,
      duration_minutes: 12,
      stale_pages: null,
    });
    const [finding] = (await listOpenFindings()).items;
    expect(finding).toMatchObject({
      kind: 'decertified_source',
      subject_text: 'Ngân hàng B ↔ Ngân hàng K',
      detail: 'Dataset v41 bị thu hồi',
    });
  });

  it('reads a context data product with its members, scope and eval', async () => {
    const p = await getAnswerProduct('dp-1');
    expect(p.readiness_text).toBe('1/10 câu hỏi mẫu');
    expect(p.scope_chips).toEqual(['vùng: 1, 2']);
    expect(p.members).toEqual([
      {
        id: 'mem-1',
        member_kind: 'dataset',
        ref: 'gia_xang',
        detail: 'Dataset · chính',
        role: 'primary',
        certified: true,
      },
    ]);
    expect(p.stats).toMatchObject({
      error_book_count: 2,
      in_scope_pct: null,
      eval: { passed: 9, total: 10, threshold_pct: 95 },
    });
    // DataProductDetail has no weekly stats yet: the Theo dõi tab says so.
    expect(p.monitoring).toBeNull();
  });

  it('puts a metric together from its versions, lineage and quality results', () => {
    type S = components['schemas'];
    const version = (no: number, status: string, note: string) =>
      ({
        version: no,
        status,
        change_note: note,
        display_name: 'Nợ xấu (%)',
        expression: `NPL v${no}`,
        description: no === 4 ? 'Dư nợ nhóm 3–5 / tổng dư nợ' : null,
        unit: '%',
        valid_from: no === 4 ? '2026-03-01T00:00:00Z' : null,
      }) as S['MetricVersionView'];
    const node = (key: string, kind: string, name: string) =>
      ({ key, kind, name, id: null, hidden: false }) as S['LineageNode'];
    const edge = (source: string, target: string) => ({ source, target, via: null, via_ref: null });
    const p = readMetricProfile(
      {
        id: 'm-1',
        code: 'NPL',
        certified: true,
        current_version: 4,
        usage: { data_product_ids: ['dp-1'], knowledge_page_ids: [] },
        versions: [
          version(3, 'superseded', 'Thêm phân khúc'),
          version(5, 'pending_review', 'Cơ cấu lần 2'),
          version(4, 'certified', 'Loại vay nội bộ'),
        ],
      } as S['MetricDetail'],
      {
        root: node('m-1', 'metric', 'NPL'),
        nodes: [
          node('ds-1', 'dataset', 'fact_loan'),
          node('src-1', 'source', 'Core'),
          node('m-1', 'metric', 'NPL'),
          node('a-1', 'agent', 'Agent Rủi ro'),
        ],
        edges: [edge('ds-1', 'm-1'), edge('src-1', 'ds-1'), edge('m-1', 'a-1')],
        depth: 3,
        direction: 'both',
        grouped: false,
      },
      [{ ok: true, label: 'Đạt', text: 'not_null · không vi phạm · 01/10/2026' }],
    );
    expect(p).toMatchObject({
      name: 'Nợ xấu (%)',
      status: 'certified',
      meta_line: 'NPL · v4 · hiệu lực từ 01/03/2026 · đơn vị %',
      definition: { expression: 'NPL v4', note: 'Dư nợ nhóm 3–5 / tổng dư nợ' },
      change_notice: { title: 'Đang có bản nháp v5', text: 'Cơ cấu lần 2', draft_version: 5 },
      usage_note: 'Dùng ở 1 data product, 0 trang tri thức.',
    });
    // Source first, then what it feeds, the metric, and its uses summed up.
    expect(p.lineage.map((l) => `${l.kind}:${l.title}`)).toEqual([
      'Nguồn:Core',
      'Dataset:fact_loan',
      'Chỉ số:NPL v4',
      'Sử dụng:1 nơi dùng',
    ]);
    expect(p.agents).toEqual([{ name: 'Agent Rủi ro', state: 'Dùng chỉ số này', tone: 'idle' }]);
    expect(p.versions.map((v) => [v.version, v.status, v.effective])).toEqual([
      [5, 'draft', '—'],
      [4, 'certified', '01/03/2026'],
      [3, 'archived', '—'],
    ]);
    expect(p.usage).toEqual([{ place: 'Agent Rủi ro', kind: 'Agent', version: 'v4', last: '—' }]);
    expect(p.quality).toHaveLength(1);
  });
});
