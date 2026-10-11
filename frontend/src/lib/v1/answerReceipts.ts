import { pathId, v1Get, v1Send } from './client';

/*
 * "P01 · Biên nhận câu trả lời" (Figma 2420:15098, UC-01-07, SCR-68): the receipt of one answer.
 * Every figure in the answer leads back to the run that produced it — the query run with the
 * metric version and the dataset as-of it read, or the computation over other figures — together
 * with the model that wrote the sentences and whether it ran outside the organisation. A locked
 * receipt can be re-run at its original point in time: the figures must come back identical, the
 * words may not (the language model is not deterministic), so by default only the figures re-run.
 *
 * The system design has no resource for this yet: the types and endpoints are a PROPOSAL for the
 * backend; the mock (mocks/answerReceipts.ts) serves them.
 */
type Uuid = string;
type Timestamp = string;
type IsoDate = string;

/** PROPOSAL: a figure read from a governed metric through one query run. */
export interface ReceiptQuerySource {
  kind: 'query_run';
  query_run_id: Uuid;
  metric: { code: string; version: number; signed_by_name: string; signed_at: IsoDate };
  dataset: { code: string; version: number; as_of: IsoDate };
  /** sha256 of the compiled SQL and the semantic compiler that produced it */
  query: { sql_sha256: string; compiler: string };
  /** when the data was loaded and when it is due again; `data_until` is the last period it holds */
  freshness: { loaded_at: Timestamp; due_at: Timestamp; data_until: IsoDate | null };
}

/** PROPOSAL: a figure computed deterministically from other figures (a difference, a share). */
export interface ReceiptComputeSource {
  kind: 'compute_run';
  compute_run_id: Uuid;
  /** "hiệu hai kỳ 25/06 và 02/07" */
  description: string;
  /** "F1 và giá trần kỳ 25/06/2026 = 20.785 đ/lít" */
  inputs_text: string;
}

/**
 * PROPOSAL: one figure of the answer, in the order it appears (F1, F2…).
 * `fresh`: data within its update window; `stale`: past its due time, the value is from
 * `freshness.data_until`; `missing`: no value (period not due yet, or the dataset holds none).
 */
export interface ReceiptFigure {
  key: string;
  /** the figure as the answer printed it; null when missing */
  value_text: string | null;
  state: 'fresh' | 'stale' | 'missing';
  source: ReceiptQuerySource | ReceiptComputeSource;
}

/** PROPOSAL: the language model that wrote the answer's sentences. */
export interface ReceiptModel {
  name: string;
  /** "vLLM tại chỗ", "Nhà cung cấp A (API)" */
  provider_label: string;
  /** true when the model runs outside the organisation (a frontier API) */
  external: boolean;
  /** what was sent outside, e.g. "chỉ với dữ liệu công khai"; set when `external` */
  external_scope_text: string | null;
}

export type RerunMode = 'figures' | 'figures_and_text';

/** PROPOSAL: the outcome of re-running a receipt at its original point in time. */
export interface ReceiptRerun {
  id: Uuid;
  mode: RerunMode;
  ran_at: Timestamp;
  figures_total: number;
  figures_matched: number;
  /** the re-computed values, in figure order */
  values_text: string[];
  /** figures whose re-run value differs from the receipt */
  mismatches: { key: string; before: string | null; after: string | null }[];
  /** sha256 of the SQL and of the result rows equal the receipt's */
  hashes_match: boolean;
  /** the re-written sentences, only for `figures_and_text` */
  answer_text: string | null;
}

/** PROPOSAL: GET /v1/answers/{id}/receipt */
export interface AnswerReceipt {
  answer_id: string;
  /** "BN-20260702-000184" */
  code: string;
  /** a receipt is locked once the answer is delivered; nothing in it changes afterwards */
  status: 'locked' | 'open';
  created_at: Timestamp;
  data_product: { id: string; name: string };
  asked_by_name: string;
  /** sha256 over the canonical receipt body */
  receipt_sha256: string;
  figures: ReceiptFigure[];
  model: ReceiptModel;
  /** the most recent re-run, if any */
  last_rerun: ReceiptRerun | null;
}

/** PROPOSAL: GET /v1/answers/{id}/receipt */
export const getAnswerReceipt = async (answerId: string) =>
  v1Get<AnswerReceipt>(`/answers/${pathId(answerId)}/receipt`);

/** PROPOSAL: POST /v1/answers/{id}:rerun {mode} — re-runs at the receipt's as-of. */
export const rerunAnswer = async (answerId: string, mode: RerunMode) =>
  v1Send<ReceiptRerun>('POST', `/answers/${pathId(answerId)}:rerun`, { mode });
