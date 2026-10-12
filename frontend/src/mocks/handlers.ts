import { http, HttpResponse } from 'msw';
import type {
  AgentRun,
  RunWire,
  DecideProposal,
  HitlRequest,
  ProposalWire,
  ResolveHitl,
} from '../lib/v1/types';
import type { PlanEdit } from '../lib/v1/runPlan';
import {
  agentsFixture,
  dataSpacesFixture,
  datasetsFixture,
  inboxFixture,
  metricsFixture,
  modelsFixture,
  myRulesFixture,
  proposalsFixture,
  runsFixture,
  sourcesFixture,
  termsFixture,
  usersFixture,
  userWire,
  workspacesFixture,
} from './fixtures';
import { createAdminAccessHandlers } from './adminAccess';
import { createDataPrepHandlers } from './dataPrep';
import { createAnswerProductHandlers } from './answerProducts';
import { createDataProductsHandlers } from './dataProducts';
import { createErrorBookHandlers } from './errorBook';
import { createExecHandlers } from './exec';
import { createDocumentHandlers } from './documents';
import { createExploreHandlers } from './explore';
import { createForecastHandlers } from './forecast';
import { createSkillAuthoringHandlers } from './skillAuthoring';
import { createSkillsHandlers } from './skills';
import { createAdminPlatformHandlers, modelWire } from './adminPlatform';
import { createAgentBuilderHandlers } from './agentBuilder';
import { createAgentOpsHandlers } from './agentOps';
import { createAlertHandlers } from './alerts';
import { createAnswerReceiptHandlers } from './answerReceipts';
import { createFeedbackHandlers } from './feedback';
import { createQuestionHandlers } from './questions';
import { createDashboardHandlers } from './dashboards';
import { createAuthorityHandlers } from './authorities';
import { createCollaborationHandlers } from './collaboration';
import { createCrawlHandlers } from './crawl';
import { createExampleHandlers } from './examples';
import { createHomeHandlers } from './home';
import { createInboxHandlers } from './inbox';
import { createKnowledgeHandlers } from './knowledge';
import { createMetricCatalogHandlers } from './metricCatalog';
import { createMetricHandlers } from './metrics';
import { createMonitorHandlers } from './monitors';
import { createNotificationHandlers } from './notifications';
import { createPackHandlers } from './packs';
import { createPeriodHandlers } from './periods';
import { createReportHandlers } from './reports';
import { createSemanticLayerHandlers } from './semanticLayer';
import { createRuleHandlers } from './rules';
import { createSessionHandlers } from './sessions';
import { createSourcesHandlers } from './sources';

/*
 * MSW handlers for the v1 endpoints the screens call (lib/v1/api.ts). State lives in memory for
 * the page's lifetime: resolving a request removes it from the inbox, as the backend would.
 * Errors follow A1.7 (application/problem+json).
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    {
      type: `https://docs.dataagent/errors/${code}`,
      title: code,
      status,
      code,
      detail,
      trace_id: 'mock',
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createHandlers() {
  let inbox: HitlRequest[] = inboxFixture();
  let proposals: ProposalWire[] = proposalsFixture();
  let runs: RunWire[] = runsFixture();
  let agents = agentsFixture();

  return [
    // First: these serve paths the handlers below also answer, with richer rows
    // (/runs/:id for test runs, /sources, /admin/models, /admin/users, /data-spaces, /policies),
    // and pass the rest through.
    // documents: /documents/:id for the ids it holds (before sources), its own HITL request;
    // explore: marks a requested Data Space, then falls through to the access-request mock;
    // forecast: its own HITL request; skills before skill authoring (shared /skills/:id).
    ...createAnswerReceiptHandlers(),
    ...createDocumentHandlers(),
    ...createExploreHandlers(),
    ...createForecastHandlers(),
    ...createSkillsHandlers(),
    ...createSkillAuthoringHandlers(),
    ...createDataPrepHandlers(),
    // Before the P06 shared product mock: it answers /v1/data-products?layer=context and adp-* ids only.
    ...createAnswerProductHandlers(),
    ...createDataProductsHandlers(),
    ...createAdminAccessHandlers(),
    ...createAgentBuilderHandlers(),
    ...createSourcesHandlers(),
    ...createAdminPlatformHandlers(),
    ...createAgentOpsHandlers(),
    ...createSessionHandlers(),
    ...createDashboardHandlers(),
    ...createFeedbackHandlers(),
    ...createQuestionHandlers(),
    // Before the inbox: the period mock answers its own reconciliation HITL request.
    ...createPeriodHandlers(),
    ...createRuleHandlers(),
    // Before the metric mock: it answers GET /v1/metrics?view=catalog and falls through otherwise.
    ...createMetricCatalogHandlers(),
    ...createMetricHandlers(),
    ...createExampleHandlers(),
    ...createAuthorityHandlers(),
    ...createKnowledgeHandlers(),
    ...createCrawlHandlers(),
    ...createMonitorHandlers(),
    ...createAlertHandlers(),
    ...createReportHandlers(),
    ...createPackHandlers(),
    ...createSemanticLayerHandlers(),
    ...createErrorBookHandlers(),
    ...createExecHandlers(),
    ...createCollaborationHandlers(),
    ...createInboxHandlers(),
    ...createHomeHandlers(),

    http.get('*/v1/inbox', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status') ?? 'open';
      return page(inbox.filter((item) => item.status === status));
    }),

    http.get('*/v1/hitl-requests/:id', ({ params }) => {
      const item = inbox.find((entry) => entry.id === params.id);
      return item ? HttpResponse.json(item) : problem(404, 'not_found', 'Không tìm thấy yêu cầu.');
    }),

    http.get('*/v1/proposals/:id', ({ params }) => {
      const item = proposals.find((entry) => entry.id === params.id);
      return item ? HttpResponse.json(item) : problem(404, 'not_found', 'Không tìm thấy đề xuất.');
    }),

    http.post('*/v1/proposals/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'decide') return problem(404, 'not_found', 'Không có thao tác này.');
      const item = proposals.find((entry) => entry.id === id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy đề xuất.');
      if (item.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Đề xuất đã được xử lý.');
      const body = (await request.json()) as DecideProposal;
      if (body.decision === 'reject' && !body.note?.trim())
        return problem(422, 'validation_failed', 'Từ chối cần ghi lý do.');
      const proposalStatus =
        body.decision === 'reject'
          ? 'rejected'
          : body.decision === 'request_changes'
            ? 'open'
            : 'accepted';
      const requestStatus =
        body.decision === 'reject'
          ? 'rejected'
          : body.decision === 'request_changes'
            ? 'changes_requested'
            : 'approved';
      const decided: ProposalWire = { ...item, status: proposalStatus };
      proposals = proposals.map((entry) => (entry.id === id ? decided : entry));
      // The request that asked for this review leaves the inbox with it.
      inbox = inbox.map((entry) =>
        entry.source_ref === id && entry.status === 'open'
          ? { ...entry, status: requestStatus, resolved_at: new Date().toISOString() }
          : entry,
      );
      return HttpResponse.json(decided);
    }),

    http.post('*/v1/hitl-requests/:action', async ({ params, request }) => {
      // MSW reads ":resolve" as part of the id segment: "<id>:resolve".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'resolve') return problem(404, 'not_found', 'Không có thao tác này.');
      const item = inbox.find((entry) => entry.id === id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy yêu cầu.');
      if (item.status !== 'open')
        return problem(409, 'invalid_state', 'Yêu cầu đã được xử lý hoặc đã hết hạn.');
      const body = (await request.json()) as ResolveHitl;
      const status =
        body.decision === 'approve'
          ? 'approved'
          : body.decision === 'reject'
            ? 'rejected'
            : body.decision === 'reassign'
              ? 'reassigned'
              : 'answered';
      const resolved: HitlRequest = { ...item, status, resolved_at: new Date().toISOString() };
      inbox = inbox.map((entry) => (entry.id === id ? resolved : entry));
      return HttpResponse.json(resolved);
    }),

    http.get('*/v1/agents', () => page(agents)),
    // PROPOSAL :lock / :unlock (lib/v1/agentControl.ts): a locked agent runs nothing and its open
    // HITL requests go to the owner.
    http.post('*/v1/agents/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const agent = agents.find((a) => a.id === id);
      if (!agent || (verb !== 'lock' && verb !== 'unlock'))
        return new HttpResponse(null, { status: 404 });
      const original = agentsFixture().find((a) => a.id === id)!;
      const next =
        verb === 'lock'
          ? {
              ...agent,
              status: 'paused' as const,
              paused_reason: 'locked',
              active_runs: 0,
              waiting_runs: 0,
              now: { text: 'Đã khoá · không chạy lượt hay trigger nào', tone: 'normal' as const },
            }
          : original;
      agents = agents.map((a) => (a.id === id ? next : a));
      return HttpResponse.json(next);
    }),
    http.get('*/v1/runs', ({ request }) => {
      const params = new URL(request.url).searchParams;
      const status = params.get('status');
      const agentId = params.get('agent_id');
      return page(
        runs.filter(
          (run) => (!status || run.status === status) && (!agentId || run.agent_id === agentId),
        ),
      );
    }),
    http.get('*/v1/runs/:id', ({ params, request }) => {
      const run = runs.find((r) => r.id === params.id);
      if (!run) return problem(404, 'not_found', 'Không tìm thấy lượt chạy.');
      const technical = new URL(request.url).searchParams.get('view') === 'technical';
      return HttpResponse.json(technical ? run : { ...run, steps: undefined });
    }),
    // "P13 · Can thiệp kế hoạch": a stopped run takes a new plan version and runs on from it.
    http.put('*/v1/runs/:id/plan', async ({ params, request }) => {
      const run = runs.find((r) => r.id === params.id);
      if (!run) return problem(404, 'not_found', 'Không tìm thấy lượt chạy.');
      if (!['paused', 'waiting_human'].includes(run.status))
        return problem(409, 'invalid_state', 'Lượt chạy chưa dừng ở ranh giới bước.');
      const edit = (await request.json()) as PlanEdit;
      const done = (run.plan ?? []).filter((s) => s.state === 'done');
      const ahead = edit.steps.filter((s) => !s.removed && !done.some((d) => d.id === s.id));
      const version = (run.plan_version ?? 1) + 1;
      const updated: RunWire = {
        ...run,
        status: 'running',
        plan_version: version,
        current_step: done.length + 1,
        total_steps: done.length + ahead.length,
        plan: [
          ...done,
          ...ahead.map((s, i) => ({
            id: s.id ?? `ps-new-${i}`,
            title: s.title,
            agent_name: s.agent_name,
            state: i === 0 ? ('running' as const) : ('waiting' as const),
          })),
        ],
      };
      runs = runs.map((r) => (r.id === run.id ? updated : r));
      return HttpResponse.json({
        run_id: run.id,
        version,
        steps: edit.steps,
        edit_note: edit.edit_note ?? null,
        edited_by_user: true,
      });
    }),
    http.post('*/v1/runs/:id/:action', async ({ params, request }) => {
      if (params.action !== 'plan:estimate')
        return problem(404, 'not_found', 'Không có thao tác này.');
      const edit = (await request.json()) as PlanEdit;
      const added = edit.steps.filter((s) => !s.id && !s.removed).length;
      const removed = edit.steps.filter((s) => s.removed).length;
      const seconds = Math.max(0, 50 + added * 30 - removed * 20);
      const parts = [
        `Ước tính thêm ${Math.floor(seconds / 60)} phút ${seconds % 60} giây`,
        `${(added * 8000 + 4000).toLocaleString('vi-VN')} đ`,
      ];
      if (edit.steps.some((s) => s.removed && s.agent_name === 'Agent Báo cáo'))
        parts.push('bản tin tuần sẽ không nhận kết quả này');
      return HttpResponse.json({ text: parts.join(' · ') });
    }),
    http.post('*/v1/runs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const run = runs.find((r) => r.id === id);
      if (!run) return problem(404, 'not_found', 'Không tìm thấy lượt chạy.');
      const next: Record<string, { from: string[]; to: AgentRun['status'] }> = {
        pause: { from: ['running', 'planning', 'queued'], to: 'paused' },
        resume: { from: ['paused'], to: 'running' },
        cancel: {
          from: ['running', 'planning', 'queued', 'paused', 'waiting_human'],
          to: 'cancelled',
        },
      };
      // PROPOSAL :retry — a failed run again, as a new queued run (202).
      if (verb === 'retry') {
        if (run.status !== 'failed')
          return problem(409, 'invalid_state', 'Chỉ chạy lại được lượt chạy lỗi.');
        const retried: RunWire = {
          ...run,
          id: `${run.id}-retry-${runs.length}`,
          code: `#${430 + runs.length}`,
          status: 'queued',
          started_at: new Date().toISOString(),
          finished_at: null,
          error: null,
          stop_reason: null,
          failure: undefined,
        };
        runs = [retried, ...runs];
        return HttpResponse.json(retried, { status: 202 });
      }
      const rule = next[verb];
      if (!rule) return problem(404, 'not_found', 'Không có thao tác này.');
      if (!rule.from.includes(run.status))
        return problem(409, 'invalid_state', 'Lượt chạy không ở trạng thái cho phép thao tác này.');
      const updated: RunWire = {
        ...run,
        status: rule.to,
        finished_at: rule.to === 'cancelled' ? new Date().toISOString() : run.finished_at,
        stop_reason: rule.to === 'cancelled' ? 'user_cancelled' : run.stop_reason,
      };
      runs = runs.map((r) => (r.id === id ? updated : r));
      return HttpResponse.json(updated);
    }),
    http.get('*/v1/workspaces', () => page(workspacesFixture())),
    http.get('*/v1/sources', () => page(sourcesFixture())),
    http.get('*/v1/datasets', () => page(datasetsFixture())),
    http.get('*/v1/metrics', () => page(metricsFixture())),
    http.get('*/v1/terms', () => page(termsFixture())),
    http.get('*/v1/policies', () => page(myRulesFixture())),
    http.get('*/v1/data-spaces', () => page(dataSpacesFixture())),
    http.get('*/v1/admin/users', () => page(usersFixture().map(userWire))),
    http.get('*/v1/admin/models', () => HttpResponse.json(modelsFixture().map(modelWire))),
    ...createNotificationHandlers(),
  ];
}
