import { pathId, v1Get, v1Send } from './client';
import type { BehaviorPolicy } from './types';

/*
 * "P07 · Quy tắc từ quyết định" (2302:19305, UC-07-08): a behaviour policy drafted from a
 * person's decision in Cần bạn ("remember as rule"), backtested on past cases, then sent to its
 * owner. A2 has GET /v1/policies, :backtest (202, window_days) and :activate.
 */
export interface DecisionRule extends Omit<BehaviorPolicy, 'status'> {
  /** PROPOSAL: pending_review = sent to its owner, waiting for :activate. */
  status: BehaviorPolicy['status'] | 'pending_review';
  /** PROPOSAL: the rule as a sentence for the title. */
  title: string;
  /** PROPOSAL: the definition in words, KHI … THÌ … NGƯỢC LẠI, one clause per line. */
  rule_text: string;
  /** PROPOSAL: "Tạo từ quyết định của Trần Quốc An lúc 09:40 (thẻ “So sánh hai nguồn” …)". */
  origin_label: string;
  /** PROPOSAL: the latest policy_backtest in the screen's terms. */
  backtest: {
    window_days: number;
    /** What a case is for this rule: "Vụ lệch dư nợ". */
    case_label: string;
    cases: number;
    auto_handled: number;
    asked_people: number;
    asked_reason: string;
    /** Past cases where the rule would have chosen differently from the person. */
    disagreements: { case_text: string; person_chose: string; rule_chooses: string }[];
  } | null;
  scope_label: string;
  owner: { id: string; name: string };
  expires_at: string | null;
  review_reminder_days: number;
}

/** PROPOSAL: GET /v1/policies/{id} (A2 lists policies but has no single read). */
export const getDecisionRule = async (id: string) => v1Get<DecisionRule>(`/policies/${pathId(id)}`);

export const backtestDecisionRule = async (id: string, windowDays = 30) =>
  v1Send<{ status: 'queued' }>('POST', `/policies/${pathId(id)}:backtest`, {
    window_days: windowDays,
  });

/** PROPOSAL: :submit sends the draft to its owner, who activates it. */
export const submitDecisionRule = async (id: string) =>
  v1Send<DecisionRule>('POST', `/policies/${pathId(id)}:submit`);

/** PROPOSAL: :disable (also proposed for access policies) drops the draft. */
export const discardDecisionRule = async (id: string) =>
  v1Send<DecisionRule>('POST', `/policies/${pathId(id)}:disable`);
