import { formatDate, formatNumber } from '../formatVN';
import { currentDataSpace, V1Error, type Page, api, ok } from './client';
import type { components } from './schema';

/*
 * Error Book (P07, UC-07-14, eval.error_book_entry): every confirmed wrong answer and forecast
 * miss as one entry with its error kind, root cause, the fix (a proposal, commit, model config or
 * dataset) and a regression case that runs in CI; open → fixed → regression_added (BR-179).
 * A2 lists the endpoints; the screen's shape is read from the api's (readErrorBookEntry).
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

export type ErrorBookViewWire = components['schemas']['ErrorBookView'];
/** GET /v1/error-book-entries/{id}; the writes answer the ErrorBookView part of it. */
export type ErrorBookDetailWire = components['schemas']['ErrorBookDetail'];

/** A2: GET /v1/error-book-entries — ErrorBookView, newest first. */
export interface ErrorBookSummary {
  id: string;
  /** entry_no, "EB-0042" */
  code: string;
  title: string;
  status: EntryStatus;
  error_kind: ErrorKind | null;
  source_kind: SourceKind;
  data_product_id: string | null;
  /** The api names the data product and the handler by id only: null until it serves the names. */
  data_product_name: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  created_at: string;
}

/** A2: GET /v1/error-book-entries/{id} — with the regression case and its last CI result. */
export interface ErrorBookEntry extends ErrorBookSummary {
  wrong: { value: string; note: string } | null;
  right: { value: string; note: string } | null;
  /** The wrong answer; `answer_id` opens its receipt (Biên nhận câu trả lời, SCR-68). */
  receipt: { answer_id: string; text: string } | null;
  root_cause: string | null;
  fix: { kind: FixKind; label: string; ref: string; is_proposal: boolean } | null;
  regression: { text: string; ci_text: string; passed: boolean | null } | null;
  /** Mở 02/07/2026 → Đã sửa 09/07/2026 → Đã có hồi quy 11/07/2026 */
  timeline: { label: string; at: string }[];
  history_text: string;
  dismissed_reason: string | null;
}

const record = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

/** A number or a short text as the screen shows it; `{value: …}` is read through. */
const shown = (value: unknown): string | null => {
  if (typeof value === 'number') return formatNumber(value);
  if (typeof value === 'string' && value.trim()) return value;
  const inner = record(value).value;
  return inner === undefined ? null : shown(inner);
};

/*
 * The wrong and the right number. A forecast miss carries them in forecast_detail (the ledger's
 * point, 95% interval and actual, BR-186); another entry only when whoever opened it wrote
 * `actual` / `expected` into its detail. Neither: the screen leaves the pair out.
 */
function numbersOf(raw: ErrorBookViewWire): Pick<ErrorBookEntry, 'wrong' | 'right'> {
  const forecast = record(raw.forecast_detail);
  const detail = record(raw.detail);
  const point = shown(forecast.point);
  const actual = shown(forecast.actual);
  if (point || actual) {
    const lo = shown(forecast.lo95);
    const hi = shown(forecast.hi95);
    const period = typeof forecast.period === 'string' ? forecast.period : null;
    return {
      wrong: point
        ? { value: point, note: lo && hi ? `dự báo · khoảng 95% ${lo}–${hi}` : 'dự báo' }
        : null,
      right: actual
        ? { value: actual, note: period ? `số thực tế kỳ ${formatDate(period)}` : 'số thực tế' }
        : null,
    };
  }
  const wrong = shown(detail.actual);
  const right = shown(detail.expected);
  const asOf = typeof detail.as_of === 'string' ? `as_of ${formatDate(detail.as_of)}` : '';
  return {
    wrong: wrong ? { value: wrong, note: 'câu trả lời sai' } : null,
    right: right ? { value: right, note: asOf } : null,
  };
}

const FIX_LABEL = (kind: string) => FIX_KINDS.find((k) => k.value === kind)?.label ?? kind;

function timelineOf(raw: ErrorBookDetailWire): ErrorBookEntry['timeline'] {
  const steps = [{ label: 'Mở', at: formatDate(raw.created_at) }];
  if (raw.fixed_at) steps.push({ label: 'Đã sửa', at: formatDate(raw.fixed_at) });
  if (raw.status === 'regression_added')
    steps.push({
      label: 'Đã có hồi quy',
      at: raw.eval_item ? formatDate(raw.eval_item.created_at) : '',
    });
  if (raw.status === 'dismissed') steps.push({ label: 'Bỏ qua', at: formatDate(raw.updated_at) });
  return steps;
}

const readErrorBookSummary = (raw: ErrorBookViewWire): ErrorBookSummary => ({
  id: raw.id,
  code: raw.entry_no,
  title: raw.title,
  status: raw.status as EntryStatus,
  error_kind: (raw.error_kind as ErrorKind | null) ?? null,
  source_kind: raw.source_kind as SourceKind,
  data_product_id: raw.data_product_id,
  data_product_name: null,
  assignee_id: raw.assignee_id,
  assignee_name: null,
  created_at: raw.created_at,
});

/**
 * The api's entry in the screen's shape. Only the detail read carries the eval item and its last
 * result; a write answers the bare entry, read the same way with what it lacks left empty.
 */
export function readErrorBookEntry(raw: ErrorBookDetailWire): ErrorBookEntry {
  const result = raw.latest_result ?? null;
  const note = record(raw.detail).fix_note;
  return {
    ...readErrorBookSummary(raw),
    ...numbersOf(raw),
    receipt: raw.answer_id
      ? {
          answer_id: raw.answer_id,
          text: raw.replay_id ? 'đã chạy lại để tái lập' : 'chưa chạy lại',
        }
      : null,
    root_cause: raw.root_cause,
    fix: raw.fix_kind
      ? {
          kind: raw.fix_kind as FixKind,
          label: FIX_LABEL(raw.fix_kind),
          ref: raw.fix_ref ?? raw.fix_proposal_id ?? '',
          is_proposal: raw.fix_proposal_id !== null,
        }
      : null,
    regression: raw.eval_item_id
      ? {
          text: raw.eval_item?.as_of
            ? `ca hồi quy as_of ${formatDate(raw.eval_item.as_of)}`
            : 'ca hồi quy trong bộ đánh giá',
          ci_text: result
            ? `Lượt CI ${formatDate(result.created_at)} ${result.passed ? 'đạt' : 'không đạt'}`
            : 'Chưa chạy',
          passed: result ? result.passed : null,
        }
      : null,
    timeline: timelineOf(raw),
    history_text: [
      raw.reopened_count > 0 ? `mở lại ${raw.reopened_count} lần` : '',
      typeof note === 'string' && note.trim() ? `ghi chú khi sửa: ${note.trim()}` : '',
    ]
      .filter(Boolean)
      .join(' · '),
    dismissed_reason: raw.dismiss_reason ?? (raw.duplicate_of_id ? 'Trùng một mục khác' : null),
  };
}

interface EntryFilters {
  status?: EntryStatus;
  error_kind?: ErrorKind;
  source_kind?: SourceKind;
  data_product_id?: string;
  assignee_id?: string;
}

// page_size is capped at 500; the screen filters and counts the whole book, so every page is read.
const PAGE_SIZE = 500;

export const listErrorBookEntries = async (
  filters: EntryFilters = {},
): Promise<Page<ErrorBookSummary>> => {
  const items: ErrorBookSummary[] = [];
  let token = '';
  do {
    const page = await ok(
      api.GET('/v1/error-book-entries', {
        params: { query: { ...filters, page_size: PAGE_SIZE, page_token: token } },
      }),
    );
    items.push(...page.items.map(readErrorBookSummary));
    token = page.next_page_token ?? '';
  } while (token);
  return { items, next_page_token: '' };
};

export const getErrorBookEntry = async (id: string) =>
  readErrorBookEntry(
    await ok(api.GET('/v1/error-book-entries/{id}', { params: { path: { id } } })),
  );

export const updateErrorBookEntry = async (
  id: string,
  patch: Partial<{ error_kind: ErrorKind; root_cause: string; title: string }>,
) =>
  readErrorBookEntry(
    await ok(api.PATCH('/v1/error-book-entries/{id}', { params: { path: { id } }, body: patch })),
  );

/** What the fix changed (MarkFixed.fix_kind), as the dialog offers it. */
export type FixKind = components['schemas']['MarkFixed']['fix_kind'];

export const FIX_KINDS: { value: FixKind; label: string }[] = [
  { value: 'metric_version', label: 'Phiên bản chỉ số' },
  { value: 'synonym', label: 'Từ đồng nghĩa' },
  { value: 'example', label: 'Câu hỏi mẫu' },
  { value: 'knowledge_page', label: 'Trang tri thức' },
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
    // A typed id that names no proposal is a wrong reference, not a failure of the page.
    const proposal = await ok(
      api.GET('/v1/proposals/{id}', { params: { path: { id: proposalId } } }),
    ).catch((error: unknown) => {
      if (error instanceof V1Error && error.status === 404)
        throw new V1Error(
          422,
          'validation_failed',
          `Không tìm thấy đề xuất ${proposalId}; dán link hoặc mã của một đề xuất đã chấp nhận, hoặc ghi tham chiếu khác (commit, model_config…).`,
        );
      throw error;
    });
    if (proposal.status !== 'accepted' && proposal.status !== 'applied')
      throw new V1Error(
        422,
        'validation_failed',
        `Đề xuất “${proposal.title}” chưa được chấp nhận; chỉ đề xuất đã chấp nhận mới là cách sửa.`,
      );
  }
  return readErrorBookEntry(
    await ok(
      api.POST('/v1/error-book-entries/{id}:mark-fixed', {
        params: { path: { id } },
        body: proposalId
          ? { fix_kind: fixKind, fix_proposal_id: proposalId }
          : { fix_kind: fixKind, fix_ref: reference },
      }),
    ),
  );
};

export const dismissEntry = async (
  id: string,
  body: { reason?: string; duplicate_of_id?: string },
) =>
  readErrorBookEntry(
    await ok(api.POST('/v1/error-book-entries/{id}:dismiss', { params: { path: { id } }, body })),
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
  return readErrorBookEntry(
    await ok(api.POST('/v1/error-book-entries', { body: { ...body, data_space_id: space } })),
  );
};
