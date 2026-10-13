/**
 * Kiểm toán as the api sends it, without the PROPOSAL display fields: an audit row read into
 * words, the screen's filters sent as GET /v1/audit's, and an export's body.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { exportAudit, searchAudit } from './adminAccess';

const row = {
  id: 'a-1',
  seq: 42,
  occurred_at: '2026-10-12T03:00:00Z',
  actor_kind: 'agent',
  actor_id: 'agent-1',
  on_behalf_of: 'u-1',
  data_space_id: null,
  service: 'worker-agent',
  action: 'query.executed',
  object_kind: 'query_run',
  object_id: '0192aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee',
  outcome: 'denied',
  before: null,
  after: null,
  agent_run_id: null,
  ip: null,
  prev_hash: 'x',
  hash: 'y',
};
let auditQuery: URLSearchParams | undefined;
let exportBody: unknown;
let usersStatus = 200;

const server = setupServer(
  http.get('*/v1/audit', ({ request }) => {
    auditQuery = new URL(request.url).searchParams;
    return HttpResponse.json({ items: [row], next_page_token: '' });
  }),
  http.get('*/v1/admin/users', () =>
    usersStatus === 200
      ? HttpResponse.json({
          items: [
            { id: 'u-1', username: 'yen', display_name: 'Đỗ Hải Yến', kind: 'human', status: 'active', last_synced_at: null },
            { id: 'agent-1', username: 'doi-soat', display_name: 'Agent Đối soát', kind: 'agent', status: 'active', last_synced_at: null },
          ],
          next_page_token: '',
        })
      : HttpResponse.json({ code: 'forbidden', detail: 'no' }, { status: usersStatus }),
  ),
  http.post(/\/v1\/audit:export$/, async ({ request }) => {
    exportBody = await request.json();
    return HttpResponse.json(
      { id: 'x-1', status: 'waiting_approval', row_count: 120_000, poll_after_ms: 2000 },
      { status: 202 },
    );
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  usersStatus = 200;
  server.resetHandlers();
});
afterAll(() => server.close());

describe('audit log', () => {
  it('reads a row into who, what and the data touched, names joined from the users', async () => {
    const page = await searchAudit({ outcome: 'denied', date: '2026-10-12' });
    expect(page.items[0]).toEqual({
      id: 'a-1',
      at: '2026-10-12T03:00:00Z',
      actor_label: 'Agent Đối soát (của Đỗ Hải Yến)',
      actor_kind: 'agent',
      action_text: 'query.executed',
      data_text: 'query_run · 0192aaaa',
      service: 'worker-agent',
      outcome: 'blocked',
    });
    expect(auditQuery?.get('outcome')).toBe('denied');
    expect(auditQuery?.get('from')).toBe(new Date(2026, 9, 12).toISOString());
    expect(auditQuery?.get('to')).toBe(new Date(2026, 9, 13).toISOString());
    // GET /v1/audit has no free text, actor kind or channel.
    expect([...(auditQuery?.keys() ?? [])].sort()).toEqual(['from', 'outcome', 'page_size', 'to']);
  });

  it('shows kind and id when the caller may not read the users', async () => {
    usersStatus = 403;
    const page = await searchAudit({});
    expect(page.items[0].actor_label).toBe('Agent agent-1 (của u-1)');
  });

  it('exports the same filters the list used, as ExportIn', async () => {
    const res = await exportAudit({ action: 'audit.searched', date: '2026-10-12' });
    expect(res.status).toBe('waiting_approval');
    expect(exportBody).toEqual({
      filters: {
        action: 'audit.searched',
        from: new Date(2026, 9, 12).toISOString(),
        to: new Date(2026, 9, 13).toISOString(),
      },
      format: 'csv',
    });
  });
});
