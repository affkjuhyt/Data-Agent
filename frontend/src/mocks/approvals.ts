import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';

type HitlView = components['schemas']['HitlView'];

/**
 * One mock's open approve_proposal requests, read each time so a decided proposal drops out.
 * On the api every proposal reaches Cần bạn through one (worker_agent/proposals.py), so several
 * mocks hold some (a skill version to publish, a document's validity) and GET
 * /v1/inbox?kind=approve_proposal must answer all of them at once.
 */
export type ApprovalSource = () => HitlView[];

/** An approve_proposal request for a proposal, in the fields the screens read. */
export const approvalRequest = (
  fields: Pick<HitlView, 'id' | 'data_space_id' | 'title' | 'source_ref' | 'created_at'> & {
    proposal_kind: string;
  },
): HitlView => ({
  id: fields.id,
  data_space_id: fields.data_space_id,
  kind: 'approve_proposal',
  risk: 'medium',
  title: fields.title,
  context: { proposal_kind: fields.proposal_kind },
  options: [],
  source_service: 'inbox',
  source_ref: fields.source_ref,
  parent_run_label: null,
  assignee_id: 'me',
  assignee_group: null,
  assignee_capability: null,
  due_at: null,
  status: 'open',
  response: null,
  remember_as_rule: false,
  resolved_by: null,
  resolved_at: null,
  channel_card_ids: [],
  created_at: fields.created_at,
  updated_at: fields.created_at,
});

/** Answers the approve_proposal inbox from every source; any other inbox read falls through. */
export const createApprovalsHandler = (sources: ApprovalSource[]) =>
  http.get('*/v1/inbox', ({ request }) => {
    if (new URL(request.url).searchParams.get('kind') !== 'approve_proposal') return undefined;
    return HttpResponse.json({ items: sources.flatMap((s) => s()), next_page_token: '' });
  });
