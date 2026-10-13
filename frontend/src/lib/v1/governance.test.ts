/**
 * Chính sách and Kiểm toán as the api sends them: GET /v1/policies is an array of the type asked
 * for, one policy is its row in it, and an audit row is said from its action, actor and object.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getAccessPolicy, searchAudit, simulatePolicy } from './adminAccess';
import { listMyRules } from './api';
import { getDecisionRule } from './rules';

const at = '2026-10-12T08:00:00Z';
let policyQuery: URLSearchParams[] = [];
let simulateBody: unknown;

const server = setupServer(
  http.get('*/v1/policies', ({ request }) => {
    const q = new URL(request.url).searchParams;
    policyQuery.push(q);
    if (q.get('type') === 'access')
      return HttpResponse.json([
        {
          id: 'pol-1',
          type: 'access',
          data_space_id: 'ds-1',
          name: 'Chi nhánh thấy chi nhánh',
          kind: 'row',
          condition_expr: 'branch_code = :user.branch',
          columns: ['id_number'],
          mask_method: 'hash',
          version: 3,
          status: 'active',
          simulated_at: at,
          activated_at: at,
        },
      ]);
    return HttpResponse.json([
      {
        id: 'rule-1',
        type: 'behavior',
        scope: q.get('scope'),
        kind: 'auto_handle',
        description: 'Lệch dưới 2 tỷ dùng Core banking',
        origin: 'hitl',
        status: q.get('status'),
        override_count: 4,
        subject_id: 'u-an',
        review_at: null,
      },
      {
        id: 'rule-2',
        type: 'behavior',
        scope: 'data_space',
        kind: 'allow',
        description: 'x',
        origin: 'manual',
        status: 'draft',
        override_count: 0,
      },
    ]);
  }),
  http.get('*/v1/data-spaces', () =>
    HttpResponse.json({ items: [{ id: 'ds-1', name: 'Rủi ro tín dụng' }], next_page_token: '' }),
  ),
  http.post('*/v1/policies/:action', async ({ request }) => {
    simulateBody = await request.json();
    return HttpResponse.json({
      policy_id: 'pol-1',
      as_user_id: 'u-1',
      applies: true,
      reason: 'phạm vi chi nhánh',
      row_condition: "branch_code = 'HN01'",
      hidden_columns: [],
      masked_columns: { id_number: 'hash' },
    });
  }),
  http.get('*/v1/admin/users', () =>
    HttpResponse.json({
      items: [
        { id: 'u-1', display_name: 'Đỗ Hải Yến', kind: 'human', status: 'active' },
        { id: 'a-1', display_name: 'Agent Đối soát', kind: 'agent', status: 'active' },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/audit', ({ request }) => {
    // GET /v1/audit filters by outcome (and action, time); the client sends the filter.
    const outcome = new URL(request.url).searchParams.get('outcome');
    const items = [
      {
        id: 'au-1',
        occurred_at: at,
        actor_kind: 'agent',
        actor_id: 'a-1',
        on_behalf_of: 'u-1',
        action: 'query.executed',
        object_kind: 'dataset',
        object_id: '0192f0aa-3333',
        outcome: 'ok',
      },
      {
        id: 'au-2',
        occurred_at: at,
        actor_kind: 'client',
        actor_id: null,
        on_behalf_of: null,
        action: 'mcp.tools_call',
        object_kind: null,
        object_id: null,
        outcome: 'denied',
      },
    ];
    return HttpResponse.json({
      items: items.filter((i) => !outcome || i.outcome === outcome),
      next_page_token: '',
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  policyQuery = [];
});
afterAll(() => server.close());

describe('governance from the api', () => {
  it('reads an access policy from the array, with its rule in lines and its Data Space', async () => {
    const p = await getAccessPolicy('pol-1');
    expect(policyQuery[0].get('type')).toBe('access');
    expect(p).toMatchObject({
      data_space_name: 'Rủi ro tín dụng',
      rule_text: 'Chỉ cho thấy dòng: branch_code = :user.branch\nCột ẩn: id_number (hash)',
    });
  });

  it('simulates as one person and compares rows and columns', async () => {
    const sim = await simulatePolicy('pol-1', { user_id: 'u-1', question: 'dư nợ theo chi nhánh' });
    expect(simulateBody).toEqual({ as_user_id: 'u-1' });
    expect(sim.rows).toEqual([
      { label: 'Dòng được thấy', without: 'tất cả', with: "branch_code = 'HN01'" },
      { label: 'Cột ẩn', without: '—', with: '—' },
      { label: 'Cột che', without: '—', with: 'id_number' },
    ]);
  });

  it('reads the user’s rules and a draft rule from decisions by type, scope and status', async () => {
    const mine = await listMyRules();
    expect(mine.items[0]).toMatchObject({ applied_this_week: 4 });
    const rule = await getDecisionRule('rule-1');
    expect(rule).toMatchObject({
      title: 'Lệch dưới 2 tỷ dùng Core banking',
      owner: { id: 'u-an' },
    });
    await expect(getDecisionRule('rule-2')).rejects.toMatchObject({ status: 404 });
  });

  it('says an audit row from its actor, action and object, and filters on the server', async () => {
    const all = await searchAudit({});
    expect(all.items[0]).toMatchObject({
      actor_label: 'Agent Đối soát (của Đỗ Hải Yến)',
      actor_kind: 'agent',
      action_text: 'query.executed',
      data_text: 'dataset · 0192f0aa',
      outcome: 'ok',
    });
    expect(all.items[1]).toMatchObject({
      actor_kind: 'client',
      outcome: 'blocked',
    });
    expect((await searchAudit({ outcome: 'denied' })).items.map((e) => e.id)).toEqual(['au-2']);
  });
});
