import { http, HttpResponse } from 'msw';
import type { AgentRun, DecideProposal, HitlRequest, Proposal, ResolveHitl } from '../lib/v1/types';
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
  workspacesFixture,
} from './fixtures';
import { createAdminAccessHandlers } from './adminAccess';
import { createAdminPlatformHandlers } from './adminPlatform';
import { createAgentBuilderHandlers } from './agentBuilder';
import { createAgentOpsHandlers } from './agentOps';
import { createAnswerReportHandlers } from './answerReports';
import { createDashboardHandlers } from './dashboards';
import { createAuthorityHandlers } from './authorities';
import { createCrawlHandlers } from './crawl';
import { createExampleHandlers } from './examples';
import { createKnowledgeHandlers } from './knowledge';
import { createMetricHandlers } from './metrics';
import { createNotificationHandlers } from './notifications';
import { createPeriodHandlers } from './periods';
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
  let proposals: Proposal[] = proposalsFixture();
  let runs: AgentRun[] = runsFixture();

  return [
    // First: these serve paths the handlers below also answer, with richer rows
    // (/runs/:id for test runs, /sources, /admin/models, /admin/users, /data-spaces, /policies),
    // and pass the rest through.
    ...createAdminAccessHandlers(),
    ...createAgentBuilderHandlers(),
    ...createSourcesHandlers(),
    ...createAdminPlatformHandlers(),
    ...createAgentOpsHandlers(),
    ...createSessionHandlers(),
    ...createDashboardHandlers(),
    ...createAnswerReportHandlers(),
    // Before the inbox: the period mock answers its own reconciliation HITL request.
    ...createPeriodHandlers(),
    ...createRuleHandlers(),
    ...createMetricHandlers(),
    ...createExampleHandlers(),
    ...createAuthorityHandlers(),
    ...createKnowledgeHandlers(),
    ...createCrawlHandlers(),

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
      const decided: Proposal = { ...item, status: proposalStatus };
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

    http.get('*/v1/agents', () => page(agentsFixture())),
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
        const retried: AgentRun = {
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
      const updated: AgentRun = {
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
    http.get('*/v1/admin/users', () => page(usersFixture())),
    http.get('*/v1/admin/models', () => page(modelsFixture())),
    ...createNotificationHandlers(),
  ];
}
