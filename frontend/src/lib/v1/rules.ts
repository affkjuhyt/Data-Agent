import { V1Error, type Page, api, ok, okAs } from './client';
import type { components } from './schema';
import { readBehaviorPolicy, type BehaviorPolicy, type BehaviorPolicyWire } from './types';

type Schemas = components['schemas'];

/*
 * "P07 · Quy tắc từ quyết định" (2302:19305, UC-07-08): a behaviour policy drafted from a
 * person's decision in Cần bạn ("remember as rule"), backtested on past cases. The draft already
 * goes to its owner through Cần bạn; the owner switches it on with :activate, anyone with the
 * right drops it with :disable {reason}. A2: GET /v1/policies (an array), GET /v1/policies/{id},
 * :backtest (202, window_days), :activate, :disable.
 */
export interface DecisionRule extends BehaviorPolicy {
  /** The rule as a sentence for the title (the policy's description). */
  title: string;
  /** KHI … THÌ … NGOẠI LỆ …, one clause per line, from condition, action and exceptions. */
  rule_text: string;
  /** "Tạo từ quyết định trong Cần bạn · 08/10/2026". */
  origin_label: string;
  created_at: string;
  /** The latest policy_backtest; A3 keeps its counts, not the cases themselves. */
  backtest: {
    window_days: number;
    cases: number;
    auto_handled: number;
    /** Matched cases the rule would not handle itself, so a person is still asked. */
    asked_people: number;
    blocked: number;
    /** Past cases where the rule would have chosen differently from the person. */
    changed: number;
  } | null;
  scope_label: string;
  /** Who switched it on; null while it is a draft. */
  approved_by: string | null;
  /** The date it must be reviewed by (review_at); null = no expiry. */
  expires_at: string | null;
}

type RuleWire = BehaviorPolicyWire & Partial<Pick<Schemas['BehaviorPolicyDetail'], 'latest_backtest'>>;

const SCOPE_LABEL: Record<string, string> = {
  system: 'Hệ thống',
  org: 'Toàn tổ chức',
  data_space: 'Data Space',
  agent: 'Agent',
  user: 'Cá nhân',
};

/** A jsonb clause in words: a string as is, `{text}` its text, otherwise "key = value" pairs. */
function clause(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v !== 'object') return String(v);
  if (Array.isArray(v)) return v.map(clause).filter(Boolean).join(' VÀ ');
  const record = v as Record<string, unknown>;
  if (typeof record.text === 'string') return record.text;
  return Object.entries(record)
    .map(([k, value]) => `${k} = ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' VÀ ');
}

const DAY_MS = 86_400_000;

function readBacktest(raw: Schemas['BacktestResultView']): NonNullable<DecisionRule['backtest']> {
  return {
    window_days: Math.max(
      1,
      Math.round((new Date(raw.window_to).getTime() - new Date(raw.window_from).getTime()) / DAY_MS),
    ),
    cases: raw.matched_count,
    auto_handled: raw.auto_handled_count,
    asked_people: Math.max(0, raw.matched_count - raw.auto_handled_count - raw.blocked_count),
    blocked: raw.blocked_count,
    changed: raw.changed_result_count,
  };
}

const readRule = (raw: RuleWire): DecisionRule => {
  const when = clause(raw.condition);
  const then = clause(raw.action);
  const except = clause(raw.exceptions);
  return {
    ...readBehaviorPolicy(raw),
    title: raw.description,
    rule_text: when
      ? [`KHI ${when}`, then && `THÌ ${then}`, except && `NGOẠI LỆ ${except}`]
          .filter(Boolean)
          .join('\n')
      : raw.description,
    origin_label:
      raw.origin === 'hitl' ? 'Tạo từ quyết định trong Cần bạn' : 'Tạo tay',
    created_at: raw.created_at,
    backtest: raw.latest_backtest ? readBacktest(raw.latest_backtest) : null,
    scope_label: SCOPE_LABEL[raw.scope] ?? raw.scope,
    approved_by: raw.approved_by ?? null,
    expires_at: raw.review_at ?? null,
  };
};

/** Behaviour policies made from decisions; `origin` is not a filter of A2, so it is applied here. */
export const listDraftRules = async (): Promise<Page<DecisionRule>> => {
  const rows = await okAs<RuleWire[]>(
    api.GET('/v1/policies', {
      params: {
        query: {
          type: 'behavior',
          scope: 'data_space',
          status: 'draft',
        },
      },
    }),
  );
  return { items: rows.map(readRule).filter((r) => r.origin === 'hitl'), next_page_token: '' };
};

/** GET /v1/policies/{id} answers either kind; an access policy is not a rule from a decision. */
export const getDecisionRule = async (id: string) => {
  const raw = await ok<RuleWire | Schemas['AccessPolicyDetail']>(
    api.GET('/v1/policies/{id}', { params: { path: { id } } }),
  );
  if (raw.type !== 'behavior') throw new V1Error(404, 'not_found', 'Không tìm thấy quy tắc.');
  return readRule(raw as RuleWire);
};

export const backtestDecisionRule = async (id: string, windowDays = 30) =>
  ok<Schemas['BacktestAccepted']>(
    api.POST('/v1/policies/{id}:backtest', {
      params: { path: { id } },
      body: {
        window_days: windowDays,
      },
    }),
  );

/** :activate — the owner switches the rule on and becomes its approver. */
export const activateDecisionRule = async (id: string) =>
  readRule(
    await okAs<RuleWire>(api.POST('/v1/policies/{id}:activate', { params: { path: { id } } })),
  );

/** :disable {reason} drops a draft or switches an active rule off; the reason is required. */
export const discardDecisionRule = async (id: string, reason: string) =>
  readRule(
    await okAs<RuleWire>(
      api.POST('/v1/policies/{id}:disable', {
        params: { path: { id } },
        body: {
          reason,
        } satisfies Schemas['PolicyDisable'],
      }),
    ),
  );
