import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Kỳ chốt (period.period_close, A2 "Kỳ chốt"): the reporting periods of a Data Space and the
 * close of one. A2 has GET /v1/periods, GET /v1/periods/{id}, :close (start, 202) and :publish
 * (mark filed and freeze). Fields and verbs marked PROPOSAL are what "P10 · Kỳ chốt · Chốt kỳ"
 * (2304:19277) needs beyond that.
 */
export type PeriodStatus = 'open' | 'closing' | 'locked' | 'published';

export interface PeriodSummary {
  id: string;
  /** "T9/2026", "Q2/2026" */
  code: string;
  status: PeriodStatus;
  /** Retro adjustments made after locking (RetroChange decided as new_version). */
  adjustment_count: number;
}

export type CloseStepState = 'done' | 'running' | 'needs_you' | 'pending';

export interface CloseStep {
  title: string;
  detail: string;
  state: CloseStepState;
  /**
   * PROPOSAL: a mismatch that stops the close, with the HITL request that decides it and its
   * options ("Chấp nhận kèm ghi chú", "Chờ sổ cái điều chỉnh"); answered with :resolve.
   */
  issue?: {
    text: string;
    hitl_id: string;
    options: { id: string; label: string; needs_note: boolean }[];
  };
}

export interface PeriodClose extends PeriodSummary {
  /** PROPOSAL: "Chốt kỳ tháng 9/2026". */
  title: string;
  /** PROPOSAL: "Data Space Rủi ro tín dụng · hạn chốt 05/10 · báo cáo NHNN phải nộp 10/10". */
  context_text: string;
  /** PROPOSAL: the close's steps (snapshot, hashes, reconciliation, lock). */
  steps: CloseStep[];
  /** PROPOSAL: the figures locking freezes, and what that means for the filed report. */
  frozen_figures: { label: string; value: string }[];
  frozen_note: string;
  /**
   * PROPOSAL: whether the caller may lock now — every step before the lock done, and not the
   * person who ran the reconciliation (four eyes).
   */
  can_lock: { allowed: boolean; reason: string | null };
}

export const listPeriods = () => v1Get<Page<PeriodSummary>>('/periods', { page_size: 500 });

export const getPeriod = async (id: string) => v1Get<PeriodClose>(`/periods/${pathId(id)}`);

/** PROPOSAL: :lock freezes the figures; afterwards every change goes through a retro change. */
export const lockPeriod = async (id: string) =>
  v1Send<PeriodClose>('POST', `/periods/${pathId(id)}:lock`);

/** PROPOSAL: :cancel-close drops the close under way and reopens the period. */
export const cancelPeriodClose = async (id: string) =>
  v1Send<PeriodClose>('POST', `/periods/${pathId(id)}:cancel-close`);

/* ─── Thay đổi hồi tố (period.retro_change, UC-10-04) ─────────────────── */

export interface RetroChange {
  id: string;
  /** PROPOSAL: "RC-012" */
  code: string;
  period_id: string;
  period_code: string;
  /** new_version / no_change once decided (A3: decision). */
  status: 'open' | 'new_version' | 'no_change';
  /** PROPOSAL: "Dữ liệu kỳ T8 thay đổi sau khi khoá". */
  title: string;
  /** PROPOSAL: when and how it was found, and what changed, in one line. */
  detected_text: string;
  /** PROPOSAL: the source rows that changed after the lock. */
  source_changes: { table: string; rows: number; fields: string; changed_at_text: string }[];
  /** PROPOSAL: each locked figure against its recalculation, and where it was published. */
  impact: {
    metric: string;
    locked: string;
    recalculated: string;
    delta: string;
    published_to: string | null;
  }[];
  /** PROPOSAL: "T8 · v2" — the version a new_version decision creates. */
  next_version_label: string;
  /** PROPOSAL: what a new version means for answers. */
  version_note: string;
  /** PROPOSAL: who else must decide when a published figure moved (null when none did). */
  escalation: { title: string; text: string } | null;
  decision_note: string | null;
}

/** PROPOSAL: GET /v1/retro-changes/{id} (A2 only lists them per period). */
export const getRetroChange = async (id: string) =>
  v1Get<RetroChange>(`/retro-changes/${pathId(id)}`);

/** A3: decision = new_version creates a new close version, the old one stays (BR-43). */
export const decideRetroChange = async (
  id: string,
  body: { decision: 'new_version' | 'no_change'; decision_note: string },
) => v1Send<RetroChange>('POST', `/retro-changes/${pathId(id)}:decide`, body);
