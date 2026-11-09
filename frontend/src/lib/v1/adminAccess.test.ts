/**
 * Yêu cầu quyền and DSR as the api sends them: AccessRequestView without the PROPOSAL display
 * fields, and a compliance officer who may read /v1/dsr but not the user list.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  decideAccessRequest,
  getAccessRequest,
  getDsr,
  listAccessRequests,
  listDsr,
} from './adminAccess';
import type { components } from './schema';

const at = '2026-10-12T08:00:00Z';
const wire: components['schemas']['AccessRequestView'] = {
  id: '0192f1a2-7c3d-7e4f-8a9b-0000000a0091',
  requester_id: 'u-chau',
  data_space_id: 'ds-1',
  capability: 'view_detail',
  reason: 'Điều tra nợ xấu',
  requested_until: '2026-11-11',
  status: 'pending',
  needs_compliance: true,
  approver_id: null,
  decided_at: null,
  decision_note: null,
  granted_scope: null,
  member_id: null,
  hitl_request_id: null,
  created_at: at,
  updated_at: at,
};

const forbidden = () =>
  HttpResponse.json(
    { code: 'forbidden', detail: 'Missing capability: admin.' },
    { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
  );

let decideSpace: string | null = null;
const server = setupServer(
  http.get('*/v1/access-requests', () => HttpResponse.json({ items: [wire], next_page_token: '' })),
  http.get('*/v1/access-requests/:id', () => HttpResponse.json(wire)),
  http.post('*/v1/access-requests/:action', ({ request }) => {
    decideSpace = request.headers.get('X-Data-Space');
    return HttpResponse.json({ ...wire, status: 'rejected', decided_at: at, approver_id: 'u-an' });
  }),
  http.get('*/v1/admin/users', () =>
    HttpResponse.json({
      items: [
        {
          id: 'u-chau',
          username: 'chau',
          display_name: 'Lê Minh Châu',
          kind: 'human',
          status: 'active',
          last_synced_at: null,
        },
        {
          id: 'u-an',
          username: 'an',
          display_name: 'Trần Quốc An',
          kind: 'human',
          status: 'active',
          last_synced_at: null,
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/data-spaces', () =>
    HttpResponse.json({
      items: [{ id: 'ds-1', code: 'RUI_RO', name: 'Rủi ro tín dụng', status: 'active' }],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  decideSpace = null;
});
afterAll(() => server.close());

describe('access requests from the api', () => {
  it('reads AccessRequestView into the screen shape, deriving the words and leaving no impact', async () => {
    const [listed] = (await listAccessRequests({ status: 'pending' })).items;
    expect(listed.impact).toEqual([]);
    expect(listed.code).toBe('AR-0A0091');
    expect(listed.title).toBe('Lê Minh Châu xin xem dữ liệu chi tiết · Rủi ro tín dụng');
    expect(listed.queue_label).toBe('Lê Minh Châu · xem dữ liệu chi tiết');
    expect(listed.scope_text).toBe('Xem dữ liệu chi tiết trong Data Space Rủi ro tín dụng');
    expect(listed.suggestion).toBeNull();
    expect(listed.due_at).toBeNull();

    const one = await getAccessRequest(wire.id);
    expect(one.impact).toEqual([]);
    expect(one.title).toBe(listed.title);
  });

  it('still reads a request when the user list is refused, naming the requester by kind', async () => {
    server.use(http.get('*/v1/admin/users', forbidden));
    const one = await getAccessRequest(wire.id);
    expect(one.title).toBe('Người dùng xin xem dữ liệu chi tiết · Rủi ro tín dụng');
  });

  it('decides in the request’s own Data Space and reads the answer the same way', async () => {
    const decided = await decideAccessRequest(
      wire.id,
      { decision: 'reject', note: 'Không' },
      'ds-1',
    );
    expect(decideSpace).toBe('ds-1');
    expect(decided.status).toBe('rejected');
    expect(decided.approver_name).toBe('Trần Quốc An');
    expect(decided.impact).toEqual([]);
  });
});

describe('DSR for a compliance officer', () => {
  const dsr = {
    id: '0192f1a2-7c3d-7e4f-8a9b-000000000014',
    request_kind: 'delete',
    received_at: at,
    due_at: at,
    status: 'review',
    handler_id: 'u-linh',
    closed_at: null,
  };

  it('lists and opens requests when /v1/admin/users answers 403', async () => {
    server.use(
      http.get('*/v1/admin/users', forbidden),
      http.get('*/v1/dsr', () => HttpResponse.json({ items: [dsr], next_page_token: '' })),
      http.get('*/v1/dsr/:id', () => HttpResponse.json({ ...dsr, items: [] })),
    );
    const page = await listDsr();
    expect(page.items.map((d) => d.handler_name)).toEqual(['u-linh']);
    expect((await getDsr(dsr.id)).code).toBe('DSR-000014');
  });
});
