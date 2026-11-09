import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listResolvedThisWeek } from './inbox';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ME = '01920000-0000-7000-8000-00000000a5c1';
const now = new Date().toISOString();
const lastMonth = new Date(Date.now() - 30 * 86_400_000).toISOString();

const hitl = (id: string, status: string, resolvedBy: string | null, resolvedAt: string) => ({
  id,
  data_space_id: null,
  kind: 'approve_proposal',
  risk: 'low',
  title: `Yêu cầu ${id}`,
  context: {},
  options: [],
  source_service: 'governance',
  source_ref: null,
  parent_run_label: null,
  assignee_id: ME,
  assignee_group: null,
  assignee_capability: null,
  due_at: null,
  status,
  response: null,
  remember_as_rule: false,
  resolved_by: resolvedBy,
  resolved_at: resolvedAt,
  channel_card_ids: [],
  created_at: resolvedAt,
  updated_at: resolvedAt,
});

describe('listResolvedThisWeek', () => {
  it('asks for each settled status the api has and keeps this week’s, by me or by no one', async () => {
    const asked: Record<string, string | null>[] = [];
    server.use(
      http.get('*/v1/me', () => HttpResponse.json({ id: ME })),
      http.get('*/v1/inbox', ({ request }) => {
        const q = new URL(request.url).searchParams;
        asked.push({
          status: q.get('status'),
          page_size: q.get('page_size'),
          since: q.get('since'),
          limit: q.get('limit'),
        });
        const items =
          q.get('status') === 'approved'
            ? [hitl('a', 'approved', ME, now), hitl('old', 'approved', ME, lastMonth)]
            : q.get('status') === 'answered'
              ? [hitl('b', 'answered', null, now), hitl('other', 'answered', 'u-x', now)]
              : [];
        return HttpResponse.json({ items, next_page_token: '' });
      }),
    );
    const page = await listResolvedThisWeek();
    expect(asked.map((a) => a.status).sort()).toEqual([
      'answered',
      'approved',
      'changes_requested',
      'rejected',
    ]);
    expect(asked.every((a) => a.page_size && !a.since && !a.limit)).toBe(true);
    expect(page.items.map((r) => [r.id, r.resolved_by, r.outcome_text])).toEqual(
      expect.arrayContaining([
        ['a', 'me', 'Đã duyệt'],
        ['b', 'rule', 'Đã trả lời'],
      ]),
    );
    expect(page.items).toHaveLength(2);
  });
});
