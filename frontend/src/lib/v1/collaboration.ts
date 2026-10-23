import { listUsers } from './api';
import { type Page, api, ok } from './client';
import type { components } from './schema';
import type { AppUser } from './types';

type Schemas = components['schemas'];

/*
 * "v3.1 · Cộng tác · Duyệt nhiều người" (2084:7085, UC-07-03): one proposal reviewed by several
 * people in turn — who proposed it, who agreed, who is reviewing, who waits — the deadline and
 * the decision log. There is no collaboration resource: the chain is read from A2's
 * GET /v1/proposals/{id} (proposer, reviews already given), the caller's own review request in
 * GET /v1/inbox (source_ref = the proposal) and its GET /v1/hitl-requests/{id} (approval_steps),
 * the log from GET /v1/audit?object_id=. Deciding goes through POST /v1/proposals/{id}:decide and
 * handing over through POST /v1/hitl-requests/{id}:resolve {decision: reassign}. UC-07-03 has no
 * discussion on a proposal: a note travels with :decide and :resolve.
 */

export type ChainStepState = 'sent' | 'agreed' | 'viewing' | 'waiting' | 'changes_requested';

export interface ChainStep {
  name: string;
  /** false for an agent. */
  person: boolean;
  role_text: string;
  state: ChainStepState;
  me: boolean;
  /** When this step was taken; null while it waits. */
  at: string | null;
}

export interface Collaboration {
  proposal_id: string;
  /** The caller's own open review request, to hand over; null when the caller has none. */
  hitl_id: string | null;
  title: string;
  status: string;
  steps: ChainStep[];
  /** The review request's due time, else the proposal's expiry. */
  deadline_at: string | null;
  log: { at: string; text: string }[];
}

type HitlDetail = Schemas['HitlDetail'];
type Proposal = Schemas['ProposalDetail'];

/** ApprovalStepView.decision and ProposalReviewView.decision, in the chain's terms. */
const DECIDED: Record<string, ChainStepState> = {
  approve: 'agreed',
  approved: 'agreed',
  accept: 'agreed',
  accept_with_edit: 'agreed',
  request_changes: 'changes_requested',
  reject: 'changes_requested',
};

const DECISION_TEXT: Record<string, string> = {
  agreed: 'đồng ý',
  changes_requested: 'yêu cầu sửa',
};

/** The caller's open request for this proposal, if the inbox holds one. */
async function myRequest(proposalId: string): Promise<HitlDetail | null> {
  const inbox = await ok<Page<Schemas['HitlView']>>(
    api.GET('/v1/inbox', { params: { query: { page_size: 500 } } }),
  );
  const open = inbox.items.find((h) => h.source_ref === proposalId && h.status === 'open');
  return open
    ? ok<HitlDetail>(api.GET('/v1/hitl-requests/{id}', { params: { path: { id: open.id } } }))
    : null;
}

/** The audit trail of the proposal; a viewer without audit access gets the derived log. */
const auditOf = async (proposalId: string) =>
  (
    await ok<Page<Schemas['AuditLogView']>>(
      api.GET('/v1/audit', {
        params: {
          query: {
            object_id: proposalId,
            page_size: 100,
          },
        },
      }),
    ).catch(() => null)
  )?.items ?? [];

function stepsOf(
  proposal: Proposal,
  request: HitlDetail | null,
  name: (id: string | null) => { name: string; person: boolean },
): ChainStep[] {
  const proposer = name(proposal.proposed_by);
  const steps: ChainStep[] = [
    { ...proposer, role_text: 'Đề xuất', state: 'sent', me: false, at: proposal.created_at },
  ];
  const approval = [...(request?.approval_steps ?? [])].sort((a, b) => a.step - b.step);
  if (approval.length) {
    // The first undecided step is the caller's: the request is in their inbox.
    const current = approval.find((s) => !s.decision);
    for (const s of approval) {
      const who = s.approver_id ? name(s.approver_id) : { name: s.approver_role, person: true };
      const state = s.decision
        ? (DECIDED[s.decision] ?? 'agreed')
        : s === current
          ? 'viewing'
          : 'waiting';
      steps.push({
        ...who,
        role_text: s.approver_id ? s.approver_role : '',
        state,
        me: s === current,
        at: s.decided_at,
      });
    }
    return steps;
  }
  for (const r of [...(proposal.reviews ?? [])].sort((a, b) => a.step - b.step))
    steps.push({
      ...name(r.reviewer_id),
      role_text: `Bước ${r.step}`,
      state: DECIDED[r.decision] ?? 'agreed',
      me: false,
      at: r.decided_at,
    });
  if (request)
    steps.push({
      name: 'Bạn',
      person: true,
      role_text: '',
      state: 'viewing',
      me: true,
      at: null,
    });
  return steps;
}

export const getCollaboration = async (proposalId: string): Promise<Collaboration> => {
  const [proposal, request, users, audit] = await Promise.all([
    ok<Proposal>(api.GET('/v1/proposals/{id}', { params: { path: { id: proposalId } } })),
    myRequest(proposalId),
    listUsers().catch(() => null),
    auditOf(proposalId),
  ]);
  const people = new Map<string, AppUser>(
    ((users?.items ?? []) as AppUser[]).map((u) => [u.id, u]),
  );
  const name = (id: string | null) => {
    const user = id ? people.get(id) : undefined;
    return { name: user?.display_name ?? '—', person: user?.kind !== 'agent' };
  };
  const steps = stepsOf(proposal, request, name);
  const log = audit.length
    ? audit.map((a) => ({
        at: a.occurred_at,
        text: `${name(a.actor_id).name} · ${a.action}`,
      }))
    : steps
        .filter((s) => s.at && s.state !== 'viewing' && s.state !== 'waiting')
        .map((s) => ({
          at: s.at as string,
          text:
            s.state === 'sent'
              ? `${s.name} tạo đề xuất`
              : `${s.name} ${DECISION_TEXT[s.state] ?? s.state}`,
        }));
  return {
    proposal_id: proposal.id,
    hitl_id: request?.id ?? null,
    title: proposal.title,
    status: proposal.status,
    steps,
    deadline_at: request?.due_at ?? proposal.expires_at ?? null,
    log: log.sort((a, b) => a.at.localeCompare(b.at)),
  };
};
