import { currentDataSpace, V1Error, type Page, api, ok, okAs } from './client';
import type { components } from './schema';

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
  | 'knowledge_lint';

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
  knowledge_lint: 'Rà soát tri thức',
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
  okAs<Page<ErrorBookSummary>>(
    api.GET('/v1/error-book-entries', { params: { query: { ...filters, limit: 200 } } }),
  );

export const getErrorBookEntry = async (id: string) =>
  okAs<ErrorBookEntry>(api.GET('/v1/error-book-entries/{id}', { params: { path: { id } } }));

export const updateErrorBookEntry = async (
  id: string,
  patch: Partial<{ error_kind: ErrorKind; root_cause: string; title: string }>,
) =>
  okAs<ErrorBookEntry>(
    api.PATCH('/v1/error-book-entries/{id}', { params: { path: { id } }, body: patch }),
  );

/** What the fix changed (MarkFixed.fix_kind), as the dialog offers it. */
export type FixKind = components['schemas']['MarkFixed']['fix_kind'];

export const FIX_KINDS: { value: FixKind; label: string }[] = [
  { value: 'metric_version', label: 'Phiên bản chỉ số' },
  { value: 'synonym', label: 'Từ đồng nghĩa' },
  { value: 'example', label: 'Câu hỏi mẫu' },
  { value: 'knowledge_page', label: 'Trang tri thức' },
  { value: 'pack_version', label: 'Phiên bản gói ngành' },
  { value: 'source_data', label: 'Dữ liệu nguồn' },
  { value: 'model_config', label: 'Cấu hình model' },
  { value: 'prompt', label: 'Prompt' },
  { value: 'code', label: 'Mã nguồn' },
  { value: 'none', label: 'Không cần sửa' },
];

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

/**
 * A2: :mark-fixed needs what was fixed and how (BR-179); it also creates the regression case.
 * A reference that holds a proposal's id — the id or its link — is that proposal, which must have
 * been accepted; any other reference (a commit, a model_config…) is kept as it is written.
 */
export const markEntryFixed = async (id: string, fixKind: FixKind, reference: string) => {
  const proposalId = reference.match(UUID)?.[0];
  if (proposalId) {
    const proposal = await ok(
      api.GET('/v1/proposals/{id}', { params: { path: { id: proposalId } } }),
    );
    if (proposal.status !== 'accepted' && proposal.status !== 'applied')
      throw new V1Error(
        422,
        'validation_failed',
        `Đề xuất “${proposal.title}” chưa được chấp nhận; chỉ đề xuất đã chấp nhận mới là cách sửa.`,
      );
  }
  return okAs<ErrorBookEntry>(
    api.POST('/v1/error-book-entries/{id}:mark-fixed', {
      params: { path: { id } },
      body: proposalId
        ? { fix_kind: fixKind, fix_proposal_id: proposalId }
        : { fix_kind: fixKind, fix_ref: reference },
    }),
  );
};

export const dismissEntry = async (
  id: string,
  body: { reason?: string; duplicate_of_id?: string },
) =>
  okAs<ErrorBookEntry>(
    api.POST('/v1/error-book-entries/{id}:dismiss', { params: { path: { id } }, body }),
  );

export const proposeEntryToPack = async (id: string, body: { pack_id: string; summary: string }) =>
  okAs<{ pack_proposal_ref: string }>(
    api.POST('/v1/error-book-entries/{id}:propose-to-pack', { params: { path: { id } }, body }),
  );

/** ErrorBookCreate requires the Data Space the entry belongs to: the one the user works in. */
export const createErrorBookEntry = async (body: {
  source_kind: SourceKind;
  title: string;
  feedback_id?: string;
  answer_id?: string;
}) => {
  const space = currentDataSpace();
  if (!space)
    throw new V1Error(400, 'invalid_request', 'Chọn một Data Space trước khi ghi vào sổ lỗi.');
  return okAs<ErrorBookEntry>(
    api.POST('/v1/error-book-entries', { body: { ...body, data_space_id: space } }),
  );
};
