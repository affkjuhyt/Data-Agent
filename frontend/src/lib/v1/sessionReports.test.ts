/**
 * A session report as the api sends it (ReportView): once approved, `approved_by` is an id and
 * the review page says the approver by name.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getSessionReport } from './sessions';

const at = '2026-11-20T08:00:00Z';
const report = (approvedBy: string | null) => ({
  id: 'r-1',
  workspace_id: 'ws-1',
  version: 1,
  title: 'Báo cáo nợ xấu tháng 10',
  body_ref: 'da-reports/r-1.md',
  checks: [],
  status: approvedBy ? 'published' : 'pending_approval',
  approved_by: approvedBy,
  published_at: approvedBy ? at : null,
  hitl_request_id: null,
  created_at: at,
  updated_at: at,
});

let answer = report(null);
const asked: string[][] = [];
const server = setupServer(
  http.get('*/v1/session-reports/r-1', () => HttpResponse.json(answer)),
  http.get('*/v1/session-reports/r-1/document', () =>
    HttpResponse.json({ markdown: '# Báo cáo nợ xấu tháng 10' }),
  ),
  http.get('*/v1/principals', ({ request }) => {
    const ids = new URL(request.url).searchParams.getAll('id');
    asked.push(ids);
    return HttpResponse.json({
      items: ids.includes('u-hoa') ? [{ id: 'u-hoa', kind: 'user', display_name: 'Lê Hoa' }] : [],
      next_page_token: '',
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  asked.length = 0;
});
afterAll(() => server.close());

describe('getSessionReport', () => {
  it('names the approver of an approved report', async () => {
    answer = report('u-hoa');
    expect((await getSessionReport('r-1')).reviewer_name).toBe('Lê Hoa');
  });

  it('names no one, and asks for no name, before the report is approved', async () => {
    answer = report(null);
    expect((await getSessionReport('r-1')).reviewer_name).toBe('');
    expect(asked).toEqual([]);
  });
});
