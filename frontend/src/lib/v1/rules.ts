import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';
import { readBehaviorPolicy, type BehaviorPolicy, type BehaviorPolicyWire } from './types';

/*
 * "P07 · Quy tắc từ quyết định" (2302:19305, UC-07-08): a behaviour policy drafted from a
 * person's decision in Cần bạn ("remember as rule"), backtested on past cases, then sent to its
 * owner. A2 has GET /v1/policies (an array, no single read), :backtest (202, window_days) and
 * :activate; the PROPOSAL fields below are read when sent and otherwise said from the row.
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

type RuleWire = BehaviorPolicyWire & Partial<Omit<DecisionRule, keyof BehaviorPolicy>>;

const readRule = (raw: RuleWire): DecisionRule => ({
  ...readBehaviorPolicy(raw),
  status: raw.status as DecisionRule['status'],
  title: raw.title ?? raw.description,
  rule_text: raw.rule_text ?? raw.description,
  origin_label: raw.origin_label ?? '',
  backtest: raw.backtest ?? null,
  scope_label: raw.scope_label ?? raw.scope,
  owner: raw.owner ?? { id: raw.subject_id ?? '', name: '' },
  expires_at: raw.expires_at ?? raw.review_at ?? null,
  review_reminder_days: raw.review_reminder_days ?? 90,
});

/** Behaviour policies made from decisions; `origin` is not a filter of A2, so it is applied here. */
const listRules = async (status?: string) => {
  const rows = await v1Get<RuleWire[]>('/policies', {
    type: 'behavior',
    scope: 'data_space',
    status,
  });
  return rows.map(readRule).filter((r) => r.origin === 'hitl');
};

/** A2: GET /v1/policies ?type ?scope ?status — the drafts made from decisions. */
export const listDraftRules = async (): Promise<Page<DecisionRule>> => ({
  items: await listRules('draft'),
  next_page_token: '',
});

/** A2 has no GET /v1/policies/{id}: a rule is its row in the list, in whatever status it is now. */
export const getDecisionRule = async (id: string) => {
  const rule = (await listRules()).find((r) => r.id === id);
  if (!rule) throw new V1Error(404, 'not_found', 'Không tìm thấy quy tắc.');
  return rule;
};

export const backtestDecisionRule = async (id: string, windowDays = 30) =>
  v1Send<components['schemas']['BacktestAccepted']>('POST', `/policies/${pathId(id)}:backtest`, {
    window_days: windowDays,
  });

/**
 * PROPOSAL: :submit sends the draft to its owner, who activates it. A2's :activate is not this:
 * it switches the rule on at once with the caller as its approver.
 */
export const submitDecisionRule = async (id: string) =>
  readRule(await v1Send<RuleWire>('POST', `/policies/${pathId(id)}:submit`));

/** PROPOSAL: :disable (also proposed for access policies) drops the draft. */
export const discardDecisionRule = async (id: string) =>
  v1Send<DecisionRule>('POST', `/policies/${pathId(id)}:disable`);
