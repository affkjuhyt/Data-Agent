import { http, HttpResponse } from 'msw';
import type { HitlRequest, ResolveHitl } from '../lib/v1/types';
import {
  agentsFixture,
  dataSpacesFixture,
  datasetsFixture,
  inboxFixture,
  metricsFixture,
  modelsFixture,
  myRulesFixture,
  runsFixture,
  sourcesFixture,
  termsFixture,
  usersFixture,
  workspacesFixture,
} from './fixtures';

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

  return [
    http.get('*/v1/inbox', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status') ?? 'open';
      return page(inbox.filter((item) => item.status === status));
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
      const status = new URL(request.url).searchParams.get('status');
      const runs = runsFixture();
      return page(status ? runs.filter((run) => run.status === status) : runs);
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
  ];
}
