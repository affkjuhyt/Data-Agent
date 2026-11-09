/**
 * Authoring a skill through A2 only. The api answers a PROPOSAL verb on a skill or a skill run
 * (POST /v1/skills/{id}:try, /v1/skill-runs/{id}:add-to-session) with 405, not 404: Starlette
 * matches GET /v1/skills/{id} with id "…:try". So nothing here may reach those paths; the
 * server below answers them the way the api does, and a call to one fails the test.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  addRunToSession,
  addSkillTestCase,
  getLatestTestRun,
  runSkill,
  runSkillTests,
  saveSkillDefinition,
  submitSkill,
  trySkill,
  type SkillDefinition,
  type SkillTestRun,
} from './skillAuthoring';

const methodNotAllowed = () =>
  HttpResponse.json(
    { type: 'about:blank', title: 'Method Not Allowed', status: 405, code: 'method_not_allowed' },
    { status: 405, headers: { 'Content-Type': 'application/problem+json' } },
  );
const notFound = () =>
  HttpResponse.json(
    { type: 'about:blank', title: 'not_found', status: 404, code: 'not_found' },
    { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
  );

const at = '2026-11-09T02:00:00Z';
const definition: SkillDefinition = {
  skill_id: 'sk-1',
  version_id: 'skv-4',
  name: 'Phân rã biến động',
  version: 4,
  status: 'draft',
  inputs: [{ name: 'metric', description: 'Chỉ số' }],
  output_text: 'Bảng đóng góp',
  steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
  instructions: 'Không suy luận nhân quả.',
  guardrails: [],
  owner: { id: 'u-1', name: '' },
  scope_label: 'org',
  domain_text: 'custom',
  used_by_text: '3 lượt/30 ngày',
  quality_text: 'Chưa kiểm định',
  test_count: 0,
  version_count: 4,
  quick_test: { params: { metric: 'NPL_RATIO' }, lines: [] },
  source: null,
};
const testRun: SkillTestRun = {
  skill_id: 'sk-1',
  version_id: 'skv-4',
  skill_name: 'Phân rã biến động',
  version: 4,
  status: 'testing',
  ran_at: at,
  cases: [],
  failures: [],
};
const versionView = (patch: Record<string, unknown>) => ({
  id: 'skv-4',
  skill_id: 'sk-1',
  version: 4,
  params_schema: { type: 'object', properties: {} },
  steps: [],
  template_md: null,
  metric_version_ids: [],
  sandbox_limits: {},
  status: 'draft',
  eval_run_id: null,
  approved_by: null,
  approved_at: null,
  approval_steps: 0,
  retire_at: null,
  created_at: at,
  ...patch,
});
const runDetail = (patch: Record<string, unknown>) => ({
  id: 'run-1',
  skill_version_id: 'skv-4',
  data_space_id: 'ds-1',
  caller_kind: 'user',
  caller_ref: null,
  principal_id: 'u-1',
  params: { metric: 'NPL_RATIO' },
  as_of: null,
  status: 'succeeded',
  result: { summary: 'Miền Bắc đóng góp nhiều nhất.' },
  result_ref: null,
  started_at: '2026-11-09T02:00:00Z',
  finished_at: '2026-11-09T02:00:11Z',
  error: null,
  created_at: at,
  skill_id: 'sk-1',
  version: 4,
  steps: [],
  ...patch,
});

let calls: string[] = [];
let versionStatus = 'draft';
let versions: Array<Record<string, unknown>> = [];
let bodies: unknown[] = [];
const record = (request: Request) =>
  calls.push(`${request.method} ${new URL(request.url).pathname}`);

const server = setupServer(
  // How the api answers the PROPOSAL paths: a verb on an id is 405, the sub-path 404.
  http.post('*/v1/skills/:action', ({ request, params }) =>
    String(params.action).includes(':') ? (record(request), methodNotAllowed()) : undefined,
  ),
  http.post('*/v1/skill-runs/:action', ({ request }) => (record(request), methodNotAllowed())),
  http.put('*/v1/skills/:id/definition', ({ request }) => (record(request), notFound())),
  http.patch('*/v1/skill-versions/:id', async ({ request }) => {
    record(request);
    const body = (await request.json()) as Record<string, unknown>;
    bodies.push(body);
    return HttpResponse.json(versionView(body));
  }),
  http.post('*/v1/skill-versions/:action', async ({ request, params }) => {
    record(request);
    bodies.push(await request.json().catch(() => null));
    const verb = String(params.action).split(':')[1];
    if (verb === 'test' && versionStatus !== 'draft' && versionStatus !== 'needs_review')
      return HttpResponse.json(
        { type: 'about:blank', title: 'invalid_state', status: 409, code: 'invalid_state' },
        { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
      );
    if (verb === 'publish') return HttpResponse.json(versionView({ status: 'pending_review' }));
    return HttpResponse.json(
      { id: verb === 'try' ? 'run-1' : 'eval-1', status: 'queued', poll_after_ms: 1 },
      { status: 202 },
    );
  }),
  http.get('*/v1/skill-runs/:id', ({ request }) => {
    record(request);
    return HttpResponse.json(
      runDetail({
        steps: [
          {
            id: 'st-1',
            seq: 1,
            name: 'Lấy giá trị hai kỳ',
            query_run_id: null,
            output: { summary: '2 kỳ · 1.204 dòng' },
            status: 'ok',
            duration_ms: 4000,
            created_at: at,
          },
          {
            id: 'st-2',
            seq: 2,
            name: 'Kiểm tra tổng',
            query_run_id: null,
            output: null,
            status: 'error',
            duration_ms: 1000,
            created_at: at,
          },
        ],
      }),
    );
  }),
  http.get('*/v1/skills/:id/test-runs/latest', notFound),
  http.get('*/v1/skills/:id', ({ request }) => {
    record(request);
    return HttpResponse.json({
      id: 'sk-1',
      name: 'Phân rã biến động',
      owner_id: 'u-1',
      scope: 'org',
      category: 'custom',
      current_version: 3,
      run_count_30d: 3,
      published_version: null,
      versions,
    });
  }),
  http.post('*/v1/skills/:id/runs', async ({ request }) => {
    record(request);
    bodies.push(await request.json());
    return HttpResponse.json({ id: 'run-1', status: 'queued', poll_after_ms: 1 }, { status: 202 });
  }),
  http.post('*/v1/skills/:id/versions', async ({ request }) => {
    record(request);
    const body = (await request.json()) as Record<string, unknown>;
    bodies.push(body);
    return HttpResponse.json(versionView({ ...body, id: 'skv-5', version: 5 }), { status: 201 });
  }),
  http.post('*/v1/skills/:id/test-cases', ({ request }) => (record(request), notFound())),
  http.post('*/v1/workspaces', async ({ request }) => {
    record(request);
    bodies.push(await request.json());
    return HttpResponse.json({ id: 'ws-9' }, { status: 201 });
  }),
  http.post('*/v1/workspaces/:id/tiles', async ({ request }) => {
    record(request);
    bodies.push(await request.json());
    return HttpResponse.json({ id: 'tile-1' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  calls = [];
  bodies = [];
  versionStatus = 'draft';
  versions = [];
});
afterAll(() => server.close());

const proposalCalls = () =>
  calls.filter((c) => /\/v1\/(skills|skill-runs)\/[^/]+:|\/definition$/.test(c));

describe('skill authoring through A2', () => {
  it('saves the draft version in place and answers it as the definition, instructions kept', async () => {
    const saved = await saveSkillDefinition(definition, {
      instructions: 'Luôn nêu kỳ so sánh.',
      steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
    });
    expect(proposalCalls()).toEqual([]);
    expect(calls).toEqual(['PATCH /v1/skill-versions/skv-4']);
    expect(bodies[0]).toEqual({
      steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
      template_md: 'Luôn nêu kỳ so sánh.',
    });
    expect(saved).toEqual({
      ...definition,
      instructions: 'Luôn nêu kỳ so sánh.',
      steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
      // What the next save starts from: the version as the api now holds it.
      source: expect.objectContaining({
        steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
      }),
    });
  });

  it('tries the draft version and reads the trial from its run', async () => {
    const trial = await trySkill(definition, { metric: 'NPL_RATIO' });
    expect(proposalCalls()).toEqual([]);
    expect(calls).toEqual(['POST /v1/skill-versions/skv-4:try', 'GET /v1/skill-runs/run-1']);
    expect(bodies[0]).toEqual({ params: { metric: 'NPL_RATIO' } });
    expect(trial).toEqual({
      version: 4,
      duration_text: '11 giây',
      steps: [
        { title: 'Lấy giá trị hai kỳ', detail: '2 kỳ · 1.204 dòng', state: 'done' },
        { title: 'Kiểm tra tổng', detail: '', state: 'error' },
      ],
      bars: [],
      conclusion: 'Miền Bắc đóng góp nhiều nhất.',
    });
  });

  it('says a version that is not a draft cannot be tried, without asking the api', async () => {
    await expect(trySkill({ ...definition, status: 'certified' }, {})).rejects.toThrow(/bản nháp/);
    expect(calls).toEqual([]);
  });

  it('sends a draft to its tests and a tested version to review through skill-versions', async () => {
    versions = [
      { id: 'skv-4', version: 4, status: 'testing', eval_run_id: 'eval-1', created_at: at },
    ];
    const rerun = await runSkillTests({ ...testRun, status: 'draft' });
    expect(calls[0]).toBe('POST /v1/skill-versions/skv-4:test');
    expect(rerun.version_id).toBe('skv-4');
    expect(rerun.status).toBe('testing');

    calls = [];
    expect(await submitSkill(testRun)).toEqual({ status: 'pending_review' });
    expect(calls).toEqual(['POST /v1/skill-versions/skv-4:publish']);
    expect(proposalCalls()).toEqual([]);
  });

  it('refuses, without asking the api, to retest a version under test or to send a draft to review', async () => {
    // skill/routes.py: :test takes draft or needs_review only, :publish a tested version only.
    await expect(runSkillTests(testRun)).rejects.toThrow(/bản nháp/);
    await expect(submitSkill({ ...testRun, status: 'draft' })).rejects.toThrow(/kiểm định/);
    expect(calls).toEqual([]);
  });

  it('works on the newest version that is not published: a draft saved after a tested one', async () => {
    versions = [
      { id: 'skv-5', version: 5, status: 'draft', eval_run_id: null, created_at: at },
      { id: 'skv-4', version: 4, status: 'testing', eval_run_id: 'eval-1', created_at: at },
      { id: 'skv-3', version: 3, status: 'published', eval_run_id: 'eval-0', created_at: at },
    ];
    const run = await getLatestTestRun('sk-1');
    expect(run).toMatchObject({ version_id: 'skv-5', version: 5, status: 'draft' });
  });

  it('says a new test case is not saved instead of rerunning the suite without it', async () => {
    await expect(
      addSkillTestCase(
        { ...testRun, status: 'draft' },
        { name: 'x', period_text: '', expected: 'y' },
      ),
    ).rejects.toThrow(/chưa lưu/);
    expect(calls).toEqual(['POST /v1/skills/sk-1/test-cases']);
  });

  it('keeps the kind of each step and the parameters of the version it edits when saving', async () => {
    const source = {
      steps: [{ seq: 1, kind: 'decompose', text: 'Cũ', tool: 'metric.query', limit: 5 }],
      params_schema: { type: 'object', properties: { metric: { title: 'Chỉ số' } } },
      metric_version_ids: ['mv-1'],
      sandbox_limits: { seconds: 30 },
    };
    await saveSkillDefinition(
      { ...definition, status: 'certified', source },
      {
        instructions: 'Mới',
        steps: [{ seq: 1, text: 'Lấy giá trị hai kỳ', tool: 'metric.query' }],
      },
    );
    expect(calls).toEqual(['POST /v1/skills/sk-1/versions']);
    expect(bodies[0]).toEqual({
      params_schema: source.params_schema,
      metric_version_ids: ['mv-1'],
      sandbox_limits: { seconds: 30 },
      steps: [
        { seq: 1, kind: 'decompose', text: 'Lấy giá trị hai kỳ', tool: 'metric.query', limit: 5 },
      ],
      template_md: 'Mới',
    });
  });

  it('runs every kind of skill through POST /v1/skills/{id}/runs', async () => {
    const run = await runSkill('sk-1', { metric: 'NPL_RATIO' }, 'decomposition');
    expect(proposalCalls()).toEqual([]);
    expect(calls).toEqual(['POST /v1/skills/sk-1/runs', 'GET /v1/skill-runs/run-1']);
    expect(run.outcome?.summary).toBe('Miền Bắc đóng góp nhiều nhất.');
  });

  it('adds a run to a new session as a skill_run tile', async () => {
    expect(await addRunToSession('run-1')).toEqual({ session_id: 'ws-9' });
    expect(proposalCalls()).toEqual([]);
    expect(calls).toEqual([
      'GET /v1/skill-runs/run-1',
      'POST /v1/workspaces',
      'POST /v1/workspaces/ws-9/tiles',
    ]);
    expect(bodies[1]).toMatchObject({ kind: 'skill_run', spec: { skill_run_id: 'run-1' } });
  });
});
