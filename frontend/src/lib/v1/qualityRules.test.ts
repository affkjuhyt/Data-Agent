/**
 * Quy tắc chất lượng as the api sends it: the rule named by its check, applied to a catalog table
 * and column, its level from A3's severity, and the past week read from its results; a new rule
 * is sent as QualityRuleCreate.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createQualityRule, listQualityRules } from './dataProducts';

const day = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString();

let created: unknown;
const server = setupServer(
  http.get('*/v1/quality-rules', () =>
    HttpResponse.json({
      items: [
        {
          id: 'qr-1',
          dataset_id: 'ds-1',
          column_name: 'outstanding_amt',
          kind: 'range',
          params: { min: 0, max: 1000 },
          severity: 'high',
          origin: 'agent_suggested',
          status: 'active',
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/datasets', () =>
    HttpResponse.json({
      items: [{ id: 'ds-1', table_name: 'loans_retail', display_name: 'Dư nợ bán lẻ' }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/quality-results', () =>
    HttpResponse.json({
      items: [
        {
          id: 'r-1',
          rule_id: 'qr-1',
          passed: true,
          severity: 'high',
          violations: 0,
          checked_at: day(2),
        },
        {
          id: 'r-2',
          rule_id: 'qr-1',
          passed: false,
          severity: 'high',
          violations: 3,
          checked_at: day(0),
        },
      ],
      next_page_token: '',
    }),
  ),
  http.post('*/v1/quality-rules', async ({ request }) => {
    created = await request.json();
    return HttpResponse.json({ id: 'qr-2' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('quality rules from the api', () => {
  it('names a rule by its check and reads its week from the results', async () => {
    const [rule] = (await listQualityRules()).items;
    expect(rule).toMatchObject({
      name: 'Trong khoảng 0 – 1000',
      target: 'Dư nợ bán lẻ.outstanding_amt',
      severity: 'block',
      owner: { name: 'Agent đề xuất' },
      current: { state: 'fail', value_text: '3 dòng vi phạm' },
    });
    expect(rule.last_7d).toEqual([null, null, null, null, 'pass', null, 'fail']);
  });

  it('sends a new rule as QualityRuleCreate', async () => {
    await createQualityRule({
      dataset_id: 'ds-1',
      kind: 'not_null',
      column_name: 'rate',
      severity: 'warn',
    });
    expect(created).toEqual({
      dataset_id: 'ds-1',
      kind: 'not_null',
      column_name: 'rate',
      severity: 'medium',
    });
  });
});
