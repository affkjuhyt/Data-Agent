import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listExamples, listSynonyms, updateExampleSql, verifyExample } from './examples';

let patchBody: unknown;

const server = setupServer(
  http.get('*/v1/examples', () =>
    HttpResponse.json({
      items: [
        {
          id: 'example-1',
          data_space_id: 'space-1',
          question: 'Nợ xấu miền Bắc?',
          sql: 'select 1',
          metric_ids: [],
          origin: 'agent_suggested',
          last_verified_at: null,
          status: 'pending_review',
          created_at: '2026-10-13T00:00:00Z',
          updated_at: '2026-10-13T00:00:00Z',
        },
        {
          id: 'example-2',
          data_space_id: 'space-1',
          question: 'Câu đã từ chối',
          sql: 'select 3',
          metric_ids: [],
          origin: 'manual',
          last_verified_at: null,
          status: 'rejected',
          created_at: '2026-10-13T00:00:00Z',
          updated_at: '2026-10-13T00:00:00Z',
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/synonyms', () =>
    HttpResponse.json({
      items: [
        {
          id: 'synonym-1',
          data_space_id: 'space-1',
          phrase: 'NPL',
          phrase_norm: 'npl',
          target_kind: 'metric',
          target_id: 'metric-1',
          target_value: null,
          origin: 'manual',
          status: 'active',
          created_at: '2026-10-13T00:00:00Z',
          updated_at: '2026-10-13T00:00:00Z',
        },
        {
          id: 'synonym-2',
          data_space_id: 'space-1',
          phrase: 'nợ xấu',
          phrase_norm: 'no xau',
          target_kind: 'metric',
          target_id: 'metric-1',
          target_value: null,
          origin: 'manual',
          status: 'active',
          created_at: '2026-10-13T00:00:00Z',
          updated_at: '2026-10-13T00:00:00Z',
        },
      ],
      next_page_token: '',
    }),
  ),
  http.patch('*/v1/examples/:id', async ({ request }) => {
    patchBody = await request.json();
    return HttpResponse.json({
      id: 'example-1',
      data_space_id: 'space-1',
      question: 'Nợ xấu miền Bắc?',
      sql: 'select 2',
      metric_ids: [],
      origin: 'agent_suggested',
      last_verified_at: null,
      status: 'active',
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('examples from the api', () => {
  it('reads ExampleView without the screen-only fields and leaves rejected ones out', async () => {
    const { items } = await listExamples();
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual({
      id: 'example-1',
      question: 'Nợ xấu miền Bắc?',
      sql: 'select 1',
      usage_text: null,
      source_label: 'Agent gợi ý',
      status: 'pending',
      reason: null,
      result_text: null,
    });
  });

  it('groups the synonyms of one target into a row', async () => {
    expect((await listSynonyms()).items).toEqual([
      { id: 'synonym-1', phrases: ['NPL', 'nợ xấu'], target_text: 'Chỉ số', scope_label: null },
    ]);
  });

  it('writes the SQL and confirms with PATCH', async () => {
    expect(await updateExampleSql('example-1', 'select 2')).toMatchObject({ sql: 'select 2' });
    expect(patchBody).toEqual({ sql: 'select 2' });
    expect(await verifyExample('example-1')).toMatchObject({ status: 'verified' });
    expect(patchBody).toEqual({ status: 'active' });
  });
});
