/**
 * Running a skill A2 serves: the designed run form is PROPOSAL, so a skill it does not know is read
 * from GET /v1/skills/{id} (the published version's params_schema), and a run is POSTed and read
 * back from GET /v1/skill-runs/{id} until it finishes.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getSkillRunForm, listSkillRuns, runSkill } from './skillAuthoring';

const notFound = () =>
  HttpResponse.json(
    { type: 'about:blank', title: 'not_found', status: 404, code: 'not_found' },
    { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
  );

let reads = 0;
let runBody: unknown;
let finished: Record<string, unknown> = { summary: 'Chi phí vốn tăng 0,2 điểm.', delta_pp: 0.2 };
const server = setupServer(
  http.get('*/v1/skills/:id/run-form', notFound),
  http.get('*/v1/skills/:id/runs', notFound),
  http.get('*/v1/skills/:id', () =>
    HttpResponse.json({
      id: 'sk-1',
      name: 'Phân tích biến động chi phí',
      current_version: 3,
      published_version: {
        id: 'skv-2',
        version: 2,
        status: 'certified',
        params_schema: {
          type: 'object',
          properties: {
            metric: { title: 'Chỉ số', enum: ['COST_RATIO', 'NPL_RATIO'] },
            period: { title: 'Kỳ', default: '2026-09' },
          },
        },
      },
    }),
  ),
  http.post('*/v1/skills/:id/runs', async ({ request }) => {
    runBody = await request.json();
    reads = 0;
    return HttpResponse.json({ id: 'run-1', status: 'queued', poll_after_ms: 1 }, { status: 202 });
  }),
  http.get('*/v1/skill-runs/:id', () => {
    reads += 1;
    return HttpResponse.json({
      id: 'run-1',
      skill_id: 'sk-1',
      version: 2,
      params: { metric: 'COST_RATIO', period: '2026-09' },
      status: reads < 2 ? 'running' : 'succeeded',
      result: reads < 2 ? null : finished,
      error: null,
      started_at: '2026-10-13T02:00:00Z',
      created_at: '2026-10-13T02:00:00Z',
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('running a skill from the api', () => {
  it('builds the form from the published version’s params_schema', async () => {
    expect(await getSkillRunForm('sk-1')).toEqual({
      skill_id: 'sk-1',
      name: 'Phân tích biến động chi phí',
      version: 2,
      status: 'certified',
      kind: 'generic',
      params: [
        {
          key: 'metric',
          label: 'Chỉ số',
          value: 'COST_RATIO',
          options: [
            { value: 'COST_RATIO', label: 'COST_RATIO' },
            { value: 'NPL_RATIO', label: 'NPL_RATIO' },
          ],
        },
        { key: 'period', label: 'Kỳ', value: '2026-09', options: [] },
      ],
      params_note: '',
    });
    expect((await listSkillRuns('sk-1')).items).toEqual([]);
  });

  it('runs it and reads the run back until it finishes', async () => {
    const run = await runSkill('sk-1', { metric: 'COST_RATIO', period: '2026-09' }, 'generic');
    expect(runBody).toEqual({ params: { metric: 'COST_RATIO', period: '2026-09' } });
    expect(run.outcome).toEqual({
      status: 'done',
      summary: 'Chi phí vốn tăng 0,2 điểm.',
      figures: [{ label: 'delta_pp', value: '0.2' }],
      error: null,
    });
  });

  it('draws a decomposition worker-agent computed as the waterfall, with its method', async () => {
    const bar = (label: string, value: number, value_text: string) => ({ label, value, value_text });
    finished = {
      summary: 'Tỷ lệ nợ xấu tăng từ 0,00% (08/2026) lên 10,00% (09/2026), +10,00 điểm %.',
      method: 'Phân rã kế toán: … không phải nguyên nhân.',
      confidence: 'Chính xác theo phép cộng (đối soát tổng: khớp)',
      decomposition: {
        summary: 'Đóng góp lớn nhất theo vùng: Miền Bắc +6,67 điểm %.',
        start: bar('08/2026', 0, '0,00%'),
        steps: [bar('Miền Bắc', 6.67, '+6,67 điểm %'), bar('Miền Nam', 3.33, '+3,33 điểm %')],
        end: bar('09/2026', 10, '10,00%'),
        contributions: [{ label: 'Miền Bắc', points_text: '+6,67 điểm %', share_text: '67%' }],
      },
      figures: { change: '10' },
    };
    const run = await runSkill('sk-1', { metric: 'NPL_RATIO', period: '2026-09' }, 'generic');
    expect(run.kind).toBe('decomposition');
    expect(run.outcome).toBeNull();
    expect(run.decomposition).toMatchObject({
      summary: 'Tỷ lệ nợ xấu tăng từ 0,00% (08/2026) lên 10,00% (09/2026), +10,00 điểm %.',
      confidence: 'Chính xác theo phép cộng (đối soát tổng: khớp)',
      end: { value_text: '10,00%' },
    });
    expect(run.decomposition?.steps).toHaveLength(2);
  });
});
