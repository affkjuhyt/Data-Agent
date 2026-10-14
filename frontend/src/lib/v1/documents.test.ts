/**
 * Tài liệu as the api sends it: a document (DocumentDetail) for the collection page and the
 * viewer, its pages, the quality queue named by its documents, the screen's choices as A2's
 * resolutions, collections, and uploads one multipart request per file.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  createCollection,
  getDocumentPage,
  getDocumentCounts,
  getDocumentView,
  getValidity,
  investigateDocument,
  listActivity,
  listChunks,
  listLibrary,
  listQualityIssues,
  askDocuments,
  resolveQualityIssue,
  setSupersession,
  uploadDocuments,
  DOCUMENT_ANSWER_PURPOSE,
} from './documents';
import { getDocument, listCollections } from './sources';

const at = '2026-10-12T08:00:00Z';
const doc = {
  id: 'd-1',
  collection_id: 'c-1',
  file_name: 'HD_0187.pdf',
  title: null,
  media: 'pdf',
  status: 'waiting_review',
  page_count: 12,
  progress: { chunks: 40, chunks_indexed: 0, pages_read: 12, pages_total: 12, pages_usable: 11 },
  blocking_quality_issues: 1,
  open_quality_issues: 2,
  effective_from: '2026-01-01',
  effective_to: null,
  error: null,
  created_at: at,
};
let bodies: unknown[] = [];
let uploads = 0;

const server = setupServer(
  http.get('*/v1/documents/:id', () => HttpResponse.json(doc)),
  http.get('*/v1/documents/:id/pages/:n', ({ params }) =>
    HttpResponse.json({
      document_id: 'd-1',
      page_no: Number(params.n),
      text: 'Điều 1',
      image_url: null,
    }),
  ),
  http.get('*/v1/collections', () =>
    HttpResponse.json({
      items: [
        {
          id: 'c-1',
          name: 'Hợp đồng tín dụng',
          media_kinds: ['pdf', 'image'],
          status: 'active',
          extraction_schema_id: null,
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/quality-issues', () =>
    HttpResponse.json({
      items: [
        {
          id: 'q-1',
          document_id: 'd-1',
          page_no: 3,
          kind: 'low_ocr',
          detail: { text: 'độ tin 0,61' },
          old_value: null,
        },
      ],
      next_page_token: '',
    }),
  ),
  http.post('*/v1/quality-issues/:action', async ({ request }) => {
    bodies.push(await request.json());
    return HttpResponse.json({});
  }),
  http.post('*/v1/collections', async ({ request }) => {
    bodies.push(await request.json());
    return HttpResponse.json({ id: 'c-2', name: 'Biên bản kiểm tra' }, { status: 201 });
  }),
  // The body is not read (see mocks/documents.ts): the second file comes back as one held.
  http.post('*/v1/documents', () => {
    uploads += 1;
    return uploads === 2
      ? HttpResponse.json({ id: 'd-1', status: 'ready' })
      : HttpResponse.json({ id: `d-${uploads + 1}`, status: 'received' }, { status: 202 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  bodies = [];
  uploads = 0;
});
afterAll(() => server.close());

describe('documents from the api', () => {
  it('reads a document for the collection page: steps, validity, status', async () => {
    const d = await getDocument('d-1');
    expect(d).toMatchObject({
      title: 'HD_0187.pdf',
      type_text: 'PDF · 12 trang',
      validity: { text: 'Từ 01/01/2026', warn: false },
      status: { tone: 'warning', text: 'Chờ người xem' },
    });
    expect(d.steps).toEqual(['done', 'done', 'done', 'done', 'warn', 'todo', 'todo', 'todo']);
  });

  it('reads the viewer’s document with its collection and signals', async () => {
    const v = await getDocumentView('d-1');
    expect(v).toMatchObject({
      kind: 'file',
      collection: { id: 'c-1', name: 'Hợp đồng tín dụng' },
      index_text: '0/40 đoạn đã lập chỉ mục',
    });
    expect(v.signals.map((s) => s.text)).toEqual(['Đọc 12/12 trang', '1 vấn đề chặn']);
    expect((await getDocumentPage('d-1', 3)).text).toBe('Điều 1');
  });

  it('names a quality issue by its document and sends A2’s resolution', async () => {
    const [issue] = (await listQualityIssues()).items;
    expect(issue).toMatchObject({
      document: { id: 'd-1', title: 'HD_0187.pdf' },
      page: 3,
      reason_text: 'Nhận dạng chữ độ tin thấp · độ tin 0,61',
    });
    await resolveQualityIssue('q-1', { action: 'edit', text: 'Điều 2' });
    await resolveQualityIssue('q-1', { action: 'correct', value: '1.200.000' });
    expect(bodies).toEqual([
      { resolution: 'corrected', corrected_text: 'Điều 2' },
      { resolution: 'value_fixed', corrected_text: '1.200.000' },
    ]);
  });

  it('lists collections and creates one as CollectionCreate', async () => {
    const [c] = (await listCollections()).items;
    expect(c).toMatchObject({
      icon: 'file',
      content_text: 'PDF, ảnh',
      extraction_text: 'Chưa có lược đồ trích',
    });
    await createCollection({
      name: 'Biên bản kiểm tra',
      file_types: 'PDF, Word',
      purpose: 'thẩm định tín dụng',
      pii_policy: 'keep_for_authorized',
      retention_days: 365,
    });
    expect(bodies[0]).toEqual({
      code: 'bien_ban_kiem_tra',
      name: 'Biên bản kiểm tra',
      purpose: 'thẩm định tín dụng',
      media_kinds: ['pdf', 'docx'],
      pii_policy: 'keep_for_authorized',
      retention_days: 365,
    });
  });

  it('uploads one request per file and counts a held one as skipped', async () => {
    const result = await uploadDocuments({
      collection_id: 'c-1',
      schema_id: null,
      pii: 'mask',
      files: [new File(['x'], 'a.pdf'), new File(['y'], 'b.pdf')],
    });
    expect(result).toEqual({ accepted: 1, skipped: 1 });
  });

  it('reads the library from DocumentListItem, filtering the PROPOSAL category here', async () => {
    let query = '';
    server.use(
      http.get('*/v1/documents', ({ request }) => {
        query = new URL(request.url).search;
        return HttpResponse.json({
          items: [
            { ...doc, step: 'reading_pages', status: 'processing', effective_from: null },
            {
              ...doc,
              id: 'd-2',
              title: 'TT 31',
              status: 'ready',
              step: 'done',
              effective_to: '2020-01-01',
            },
          ],
          next_page_token: '',
        });
      }),
    );
    const { items } = await listLibrary({ state: 'processing', collection_id: 'c-1' });
    expect(query).toContain('status=processing');
    expect(query).toContain('collection_id=c-1');
    expect(items[0]).toMatchObject({
      title: 'HD_0187.pdf',
      category: null,
      category_label: 'PDF',
      processing: { step_text: 'Đọc trang', progress: 0.5, started_at: at },
    });
    expect(items[1].validity).toEqual({
      tone: 'idle',
      text: 'Hết hiệu lực 01/01/2020',
      ended: true,
    });
    expect(await getDocumentCounts()).toEqual({
      all: 2,
      regulation: undefined,
      internal: undefined,
      processing: 1,
    });
    expect((await listLibrary({ category: 'regulation' })).items).toEqual([]);
  });

  it('lists the chunks from the pages, once each and in order', async () => {
    server.use(
      http.get('*/v1/documents/:id/pages/:n', ({ params }) =>
        HttpResponse.json({
          page_no: Number(params.n),
          chunks:
            params.n === '1'
              ? [
                  { id: 'k1', page_from: 1, text: 'a' },
                  { id: 'k2', page_from: 1, text: 'b' },
                ]
              : [
                  { id: 'k2', page_from: 1, text: 'b' },
                  { id: 'k3', page_from: 2, text: 'c' },
                ],
        }),
      ),
    );
    expect((await listChunks({ id: 'd-1', page_count: 2 })).items).toEqual([
      { id: 'k1', page: 1, ordinal: 1, text: 'a' },
      { id: 'k2', page: 1, ordinal: 2, text: 'b' },
      { id: 'k3', page: 2, ordinal: 3, text: 'c' },
    ]);
  });

  it('says the history from the versions, leaving the audit out without audit_read', async () => {
    server.use(
      http.get('*/v1/documents/:id/versions', () =>
        HttpResponse.json({
          items: [
            {
              ...doc,
              title: 'QĐ 1120',
              doc_no: '1120/QĐ',
              effective_from: '2023-04-01',
              effective_to: '2026-09-01',
              created_at: '2023-03-15T02:00:00Z',
            },
          ],
          next_page_token: '',
        }),
      ),
      http.get('*/v1/audit', () =>
        HttpResponse.json({ title: 'forbidden', status: 403, code: 'forbidden' }, { status: 403 }),
      ),
    );
    const { items } = await listActivity('d-1');
    expect(items.map((i) => i.text)).toEqual(['Nạp QĐ 1120 · 1120/QĐ · hiệu lực từ 01/04/2023']);
    const v = await getValidity('d-1');
    expect(v).toMatchObject({ title: 'QĐ 1120', owner_text: null, pending: null, impact: [] });
    expect(v.status.text).toBe('Hết hiệu lực');
    expect(v.timeline.map((e) => e.text)).toEqual([
      'QĐ 1120 · 1120/QĐ · có hiệu lực',
      'QĐ 1120 hết hiệu lực',
    ]);
  });

  it('records a supersession on both documents', async () => {
    const patches: [string, unknown][] = [];
    server.use(
      http.patch('*/v1/documents/:id', async ({ params, request }) => {
        patches.push([String(params.id), await request.json()]);
        return HttpResponse.json(doc);
      }),
    );
    await setSupersession('d-old', { superseded_by: 'd-new', valid_to: '2026-09-01' });
    expect(patches).toEqual([
      ['d-new', { supersedes_id: 'd-old' }],
      ['d-old', { effective_to: '2026-09-01' }],
    ]);
  });

  it('hands the investigation to an agent as a task', async () => {
    server.use(
      http.post('*/v1/tasks', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          { id: 't-1', status: 'queued', poll_after_ms: 1 },
          { status: 202 },
        );
      }),
    );
    expect((await investigateDocument({ id: 'd-1', title: 'Khiếu nại phí' })).id).toBe('t-1');
    expect(String((bodies[0] as { description: string }).description)).toContain('Khiếu nại phí');
  });

  it('answers from documents: Hỏi đáp writes the answer, the search cites the passages', async () => {
    let asked: unknown;
    let searched: unknown;
    server.use(
      http.post('*/v1/questions', async ({ request }) => {
        asked = await request.json();
        return HttpResponse.json(
          { id: 'q', question_id: 'q', conversation_id: 'c', status: 'received', poll_after_ms: 0 },
          { status: 202 },
        );
      }),
      http.get('*/v1/questions/:id', () =>
        HttpResponse.json({ id: 'q', status: 'answered', answer_id: 'a' }),
      ),
      http.get('*/v1/answers/:id', () =>
        HttpResponse.json({
          id: 'a',
          kind: 'document',
          text: 'Nhóm 3 [1].',
          agent_name: null,
          latency_ms: 3400,
        }),
      ),
      http.post('*/v1/search/documents', async ({ request }) => {
        searched = await request.json();
        return HttpResponse.json({
          items: [
            {
              document_id: 'd-9',
              document_title: null,
              doc_no: 'TT 31/2024',
              section_path: 'Điều 10',
              page_from: 14,
              text: 'tối thiểu là nhóm 3',
              effective_from: null,
              effective_to: '2020-01-01',
            },
          ],
          next_page_token: '',
        });
      }),
    );
    const answer = await askDocuments({ question: 'Nhóm nợ?', collection_id: 'c-1' });
    expect(asked).toEqual({
      text: 'Nhóm nợ?',
      knowledge_mode: 'latest',
      purpose: DOCUMENT_ANSWER_PURPOSE,
    });
    expect(searched).toEqual({ query: 'Nhóm nợ?', collection_ids: ['c-1'], top_k: 5 });
    expect(answer).toMatchObject({
      answer: 'Nhóm 3 [1].',
      not_found: false,
      scope_text: 'Tìm trong: bộ sưu tập đã chọn',
      meta_text: 'Agent Tài liệu · 1 nguồn · 3,4 giây',
    });
    expect(answer.citations[0]).toEqual({
      n: 1,
      document_id: 'd-9',
      title: 'TT 31/2024',
      locator: 'Điều 10 · trang 14',
      page: 14,
      validity: { tone: 'warning', text: 'Hết hiệu lực 01/01/2020' },
      superseded: true,
      quote: 'tối thiểu là nhóm 3',
    });
  });
});
