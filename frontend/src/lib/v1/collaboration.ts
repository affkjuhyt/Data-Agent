import { pathId, v1Get, v1Send } from './client';

/*
 * "v3.1 · Cộng tác · Duyệt nhiều người" (2084:7085, UC-07-03): one proposal reviewed by several
 * people in turn — who proposed it, who agreed, who is reviewing, who waits — the deadline and
 * what happens if it passes, the discussion (people and agents can be @mentioned), the decision
 * log and the approval rule. Deciding goes through A2's POST /v1/proposals/{id}:decide and
 * handing over through POST /v1/hitl-requests/{id}:resolve {decision: reassign}; reading this
 * view and commenting are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export interface Collaboration {
  proposal_id: string;
  /** The caller's own review request, to hand over. */
  hitl_id: string;
  title: string;
  steps: {
    name: string;
    /** null for an agent. */
    initials: string | null;
    role_text: string;
    state: 'sent' | 'agreed' | 'viewing' | 'waiting' | 'changes_requested';
    me: boolean;
  }[];
  deadline_text: string;
  comments: { author: string; initials: string | null; at: string; text: string }[];
  log: { at: string; text: string }[];
  rule_text: string;
}

export const getCollaboration = async (proposalId: string) =>
  v1Get<Collaboration>(`/proposals/${pathId(proposalId)}/collaboration`);

/** PROPOSAL: POST /v1/proposals/{id}/comments — "@Agent …" asks that agent to answer. */
export const commentOnProposal = async (proposalId: string, text: string) =>
  v1Send<Collaboration>('POST', `/proposals/${pathId(proposalId)}/comments`, { text });
