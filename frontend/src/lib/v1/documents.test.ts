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
  getDocumentView,
  listQualityIssues,
  resolveQualityIssue,
  uploadDocuments,
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
      source_id: 's',
      schema_id: null,
    });
    expect(bodies[0]).toEqual({
      code: 'bien_ban_kiem_tra',
      name: 'Biên bản kiểm tra',
      purpose: 'Biên bản kiểm tra',
      media_kinds: ['pdf', 'docx'],
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
});
