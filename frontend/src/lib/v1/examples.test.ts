import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listExamples, listSynonyms, updateExampleSql } from './examples';

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
          origin: 'agent',
          last_verified_at: null,
          status: 'active',
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
      ],
      next_page_token: '',
    }),
  ),
  http.patch('*/v1/examples/:id', () =>
    HttpResponse.json({
      id: 'example-1',
      data_space_id: 'space-1',
      question: 'Nợ xấu miền Bắc?',
      sql: 'select 2',
      metric_ids: [],
      origin: 'agent',
      last_verified_at: null,
      status: 'active',
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('examples from the api', () => {
  it('adapts examples and synonyms and writes example SQL', async () => {
    expect((await listExamples()).items[0]).toMatchObject({
      question: 'Nợ xấu miền Bắc?',
      status: 'verified',
    });
    expect((await listSynonyms()).items[0]).toMatchObject({
      phrases: ['NPL'],
      target_text: 'metric',
      scope_label: 'Đã xác nhận',
    });
    expect(await updateExampleSql('example-1', 'select 2')).toMatchObject({ sql: 'select 2' });
  });
});
