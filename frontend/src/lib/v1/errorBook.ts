import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Error Book (P07, UC-07-14, eval.error_book_entry): every confirmed wrong answer and forecast
 * miss as one entry with its error kind, root cause, the fix (a proposal, commit, model config or
 * dataset) and a regression case that runs in CI; open → fixed → regression_added (BR-179).
 * A2 lists the endpoints; the display fields beside the spec's are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export type EntryStatus = 'open' | 'fixed' | 'regression_added' | 'dismissed';
export type ErrorKind =
  | 'definition'
  | 'synonym'
  | 'data'
  | 'freshness'
  | 'model'
  | 'formula'
  | 'retrieval';
export type SourceKind =
  | 'user_report'
  | 'number_check'
  | 'reviewer_reject'
  | 'replay_mismatch'
  | 'forecast_miss'
  | 'lint_finding';

export const ERROR_KINDS: { value: ErrorKind; label: string }[] = [
  { value: 'definition', label: 'Định nghĩa' },
  { value: 'synonym', label: 'Đồng nghĩa' },
  { value: 'data', label: 'Dữ liệu' },
  { value: 'freshness', label: 'Độ tươi' },
  { value: 'model', label: 'Mô hình' },
  { value: 'formula', label: 'Công thức' },
  { value: 'retrieval', label: 'Truy hồi' },
];

export const SOURCE_LABEL: Record<SourceKind, string> = {
  user_report: 'Báo sai của người dùng',
  number_check: 'Kiểm số thất bại',
  reviewer_reject: 'Người duyệt bác',
  replay_mismatch: 'Chạy lại lệch',
  forecast_miss: 'Dự báo lệch',
  lint_finding: 'Rà soát tri thức',
};

/** A2: GET /v1/error-book-entries — ErrorBookEntry, newest first. */
export interface ErrorBookSummary {
  id: string;
  code: string;
  title: string;
  status: EntryStatus;
  error_kind: ErrorKind | null;
  source_kind: SourceKind;
  data_product_name: string | null;
  assignee_name: string;
  created_at: string;
}

/** A2: GET /v1/error-book-entries/{id} — with receipt, replay, fix proposal, eval item and last CI. */
export interface ErrorBookEntry extends ErrorBookSummary {
  wrong: { value: string; note: string } | null;
  right: { value: string; note: string } | null;
  /** The receipt of the wrong answer; `answer_id` opens it (Biên nhận câu trả lời, SCR-68). */
  receipt: { id: string; code: string; answer_id: string; replay_text: string } | null;
  root_cause: string | null;
  fix: {
    kind: 'proposal' | 'commit' | 'model_config' | 'dataset' | 'source_data';
    label: string;
    ref: string;
    proposal_status: 'pending' | 'accepted' | 'rejected' | null;
  } | null;
  regression: { code: string; text: string; ci_text: string; passed: boolean | null } | null;
  /** Mở 02/07 → Đã sửa 09/07 → Đã có hồi quy 11/07 */
  timeline: { label: string; at: string }[];
  history_text: string;
  /** The fix applies to every customer of this data: it can go to the industry pack. */
  pack_candidate: { pack_id: string; pack_name: string } | null;
  dismissed_reason: string | null;
}

interface EntryFilters {
  status?: EntryStatus;
  error_kind?: ErrorKind;
  source_kind?: SourceKind;
  data_product_id?: string;
  assignee_id?: string;
}

export const listErrorBookEntries = async (filters: EntryFilters = {}) =>
  v1Get<Page<ErrorBookSummary>>('/error-book-entries', { ...filters, limit: 200 });

export const getErrorBookEntry = async (id: string) =>
  v1Get<ErrorBookEntry>(`/error-book-entries/${pathId(id)}`);

export const updateErrorBookEntry = async (
  id: string,
  patch: Partial<{ error_kind: ErrorKind; root_cause: string; title: string }>,
) => v1Send<ErrorBookEntry>('PATCH', `/error-book-entries/${pathId(id)}`, patch);

/** A2: :mark-fixed needs kind, root cause and an accepted fix; it also creates the regression case. */
export const markEntryFixed = async (
  id: string,
  body: { fix_kind: string; fix_proposal_id?: string; fix_ref?: string; note?: string },
) => v1Send<ErrorBookEntry>('POST', `/error-book-entries/${pathId(id)}:mark-fixed`, body);

export const dismissEntry = async (
  id: string,
  body: { reason?: string; duplicate_of_id?: string },
) => v1Send<ErrorBookEntry>('POST', `/error-book-entries/${pathId(id)}:dismiss`, body);

export const proposeEntryToPack = async (id: string, body: { pack_id: string; summary: string }) =>
  v1Send<{ pack_proposal_ref: string }>(
    'POST',
    `/error-book-entries/${pathId(id)}:propose-to-pack`,
    body,
  );

export const createErrorBookEntry = async (body: {
  source_kind: SourceKind;
  title: string;
  detail?: string;
  feedback_id?: string;
  answer_id?: string;
}) => v1Send<ErrorBookEntry>('POST', '/error-book-entries', body);
