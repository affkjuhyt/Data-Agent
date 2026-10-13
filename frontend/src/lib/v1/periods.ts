import { pathId, V1Error, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * Kỳ chốt (period.period_close, A2 "Kỳ chốt"): the reporting periods of a Data Space and the
 * close of one. A2 has GET /v1/periods, GET /v1/periods/{id}, :close (start, 202) and :publish
 * (mark filed and freeze). There is no lock and no cancel: locking is the last step of :close,
 * taken by the worker once every item matches or its exceptions have both approvals (UC-10-02
 * step 5, BR-132), and a close that fails is closed again (E1). Fields marked PROPOSAL are what
 * "P10 · Kỳ chốt · Chốt kỳ" (2304:19277) shows beyond PeriodCloseDetail; the api's rows are read
 * into the screens' shapes below, the PROPOSAL parts come along under `display` when the mock
 * sends them and are said from the row otherwise.
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
  /** A2 :close starts the close of an open period (or one whose close failed). */
  can_start_close: boolean;
}

/** period_close.status: open, closing, close_failed, closed, filed, retro_detected. */
const STATUS: Record<string, PeriodStatus> = {
  open: 'open',
  closing: 'closing',
  close_failed: 'closing',
  closed: 'locked',
  filed: 'published',
  retro_detected: 'published',
};

type PeriodWire = Schemas['PeriodCloseView'] & { display?: PeriodSummary };
type PeriodDetailWire = Schemas['PeriodCloseDetail'] & { display?: PeriodClose };

const readSummary = (raw: PeriodWire): PeriodSummary =>
  raw.display ?? {
    id: raw.id,
    code: raw.period_label,
    status: STATUS[raw.status] ?? 'open',
    adjustment_count: Math.max(0, raw.version - 1),
  };

/*
 * Without the PROPOSAL close page: the steps are the close's own states (snapshot hashed, the
 * reconciliation counted by status, locked, filed) and nothing is said frozen.
 */
function readClose(raw: PeriodDetailWire): PeriodClose {
  if (raw.display) return raw.display;
  const summary = readSummary(raw);
  const step = (title: string, done: boolean, detail: string): CloseStep => ({
    title,
    detail,
    state: done ? 'done' : 'pending',
  });
  return {
    ...summary,
    title: `Chốt kỳ ${raw.period_label}`,
    context_text: [
      `ngày nghiệp vụ ${raw.business_date}`,
      raw.filed_report_code && `báo cáo ${raw.filed_report_code}`,
    ]
      .filter(Boolean)
      .join(' · '),
    steps: [
      step(
        'Ảnh chụp dữ liệu',
        raw.snapshot_hash !== null,
        raw.snapshot_hash ? 'đã băm' : 'chưa chụp',
      ),
      reconciliationStep(raw),
      step(
        'Khoá kỳ',
        raw.closed_at !== null,
        raw.closed_at ? `lúc ${raw.closed_at.slice(0, 16)}` : 'chưa khoá',
      ),
      step(
        'Nộp báo cáo',
        raw.filed_at !== null,
        raw.filed_at ? `lúc ${raw.filed_at.slice(0, 16)}` : 'chưa nộp',
      ),
    ],
    frozen_figures: [],
    frozen_note: '',
    can_start_close: raw.status === 'open' || raw.status === 'close_failed',
  };
}

/** period_reconciliation.status: matched, mismatched, exception_pending, exception_approved. */
function reconciliationStep(raw: PeriodDetailWire): CloseStep {
  const counts = (raw.reconciliation ?? {}) as Record<string, number>;
  const n = (status: string) => counts[status] ?? 0;
  const total = Object.values(counts).reduce((sum, v) => sum + v, 0);
  const detail = [
    n('matched') && `${n('matched')} khớp`,
    n('mismatched') && `${n('mismatched')} lệch`,
    n('exception_pending') && `${n('exception_pending')} chờ duyệt ngoại lệ`,
    n('exception_approved') && `${n('exception_approved')} ngoại lệ đã duyệt`,
  ]
    .filter(Boolean)
    .join(' · ');
  return {
    title: 'Đối chiếu sổ cái',
    detail: total ? detail : 'chưa đối chiếu',
    state: !total
      ? 'pending'
      : n('mismatched')
        ? 'needs_you'
        : n('exception_pending')
          ? 'running'
          : 'done',
  };
}

export const listPeriods = async () => {
  const page = await v1Get<Page<PeriodWire>>('/periods', { page_size: 500 });
  return { ...page, items: page.items.map(readSummary) };
};

export const getPeriod = async (id: string) =>
  readClose(await v1Get<PeriodDetailWire>(`/periods/${pathId(id)}`));

/**
 * A2: :close (202) — the worker snapshots, hashes and reconciles, then locks the period and
 * freezes its figures; afterwards every change goes through a retro change.
 */
export const startPeriodClose = async (id: string) =>
  v1Send<{ id: string; status: string; poll_after_ms?: number }>(
    'POST',
    `/periods/${pathId(id)}:close`,
  );

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

type RetroWire = Schemas['RetroChangeView'] & { display?: RetroChange };

const number = (v: number | string) => String(v).replace('.', ',');

/** a − b at the precision the two decimals came with, so 1.96 − 0.12 reads 1,84. */
const minus = (a: number | string, b: number | string) => {
  const places = Math.max(...[a, b].map((v) => String(v).split('.')[1]?.length ?? 0));
  return number((Number(a) - Number(b)).toFixed(places));
};

function readRetro(raw: RetroWire, period: PeriodSummary | undefined): RetroChange {
  if (raw.display) return raw.display;
  const label = period?.code ?? '';
  return {
    id: raw.id,
    code: `#${raw.id.slice(0, 8)}`,
    period_id: raw.period_close_id,
    period_code: label,
    status:
      raw.decision === 'new_version'
        ? 'new_version'
        : raw.decision === 'no_action'
          ? 'no_change'
          : 'open',
    title: `Dữ liệu kỳ ${label} thay đổi sau khi khoá`,
    detected_text: `Phát hiện ${raw.detected_at.slice(0, 10)} · lệch ${number(raw.delta)}${raw.below_materiality ? ' (dưới ngưỡng trọng yếu)' : ''}`,
    source_changes: [],
    // The published figure and its recalculation: current_value is now, delta what moved.
    impact: [
      {
        metric: 'Kết quả đã công bố',
        locked: minus(raw.current_value, raw.delta),
        recalculated: number(raw.current_value),
        delta: number(raw.delta),
        published_to: null,
      },
    ],
    next_version_label: `${label} · bản mới`,
    version_note: '',
    escalation: raw.compliance_notified
      ? { title: 'Đã báo tuân thủ', text: 'Số đã công bố thay đổi; bộ phận tuân thủ đã được báo.' }
      : null,
    decision_note: raw.decision_note,
  };
}

/*
 * A2 lists retro changes per period (GET /v1/periods/{id}/retro-changes) and has no read of one:
 * the open ones are gathered over the periods, one change is found among them.
 */
async function listRetroChanges(): Promise<RetroChange[]> {
  const periods = (await listPeriods()).items;
  const pages = await Promise.all(
    periods.map((p) => v1Get<Page<RetroWire>>(`/periods/${pathId(p.id)}/retro-changes`)),
  );
  return pages.flatMap((page, i) => page.items.map((r) => readRetro(r, periods[i])));
}

export const listOpenRetroChanges = async (): Promise<Page<RetroChange>> => ({
  items: (await listRetroChanges()).filter((r) => r.status === 'open'),
  next_page_token: '',
});

export const getRetroChange = async (id: string) => {
  const change = (await listRetroChanges()).find((r) => r.id === id);
  if (!change) throw new V1Error(404, 'not_found', 'Không tìm thấy thay đổi hồi tố.');
  return change;
};

/**
 * A3: decision = new_version creates a new close version, the old one stays (BR-43); the
 * screen's "no change" is A3's no_action.
 */
export const decideRetroChange = async (
  id: string,
  body: { decision: 'new_version' | 'no_change'; decision_note: string },
) =>
  v1Send<RetroWire>('POST', `/retro-changes/${pathId(id)}:decide`, {
    decision: body.decision === 'no_change' ? 'no_action' : 'new_version',
    decision_note: body.decision_note,
  } satisfies Schemas['RetroDecision']);

/* ─── So sánh hai bản (UC-10-05) ──────────────────────────────────────── */

export interface PeriodVersion {
  id: string;
  /** "T8/2026 · v1 · đã công bố" */
  label: string;
  /** "đã công bố 05/09", "điều chỉnh hồi tố 07/10" */
  note: string;
}

export interface PeriodComparison {
  left: PeriodVersion;
  right: PeriodVersion;
  /** PROPOSAL: the versions the pickers offer. */
  versions: PeriodVersion[];
  /** Every gap traces back to the retro change or run that made it (UC-10-05). */
  rows: {
    metric: string;
    left: string;
    right: string;
    delta: string;
    changed: boolean;
    reason: string | null;
    retro_change_id: string | null;
  }[];
}

type CompareWire = Schemas['CompareResult'] & { display?: PeriodComparison };

const side = (s: Schemas['CompareSide']): PeriodVersion => ({
  id: s.period_close_id,
  label: `${s.period_label} · v${s.version} · ${STATUS[s.status] === 'published' ? 'đã công bố' : 'chưa công bố'}`,
  note: s.values_from === 'retro' ? 'điều chỉnh hồi tố' : 'số đã công bố',
});

const CAUSE: Record<string, string> = {
  retro: 'điều chỉnh hồi tố',
  definition: 'đổi định nghĩa chỉ số',
  data: 'dữ liệu nguồn đổi',
  added: 'chỉ có ở bản sau',
  removed: 'chỉ có ở bản trước',
};

/** A2: GET /v1/periods/compare?a&b — two closes (versions) of a period, item by item. */
export async function comparePeriodVersions(
  left: string,
  right: string,
): Promise<PeriodComparison> {
  const raw = await v1Get<CompareWire>('/periods/compare', { a: left, b: right });
  if (raw.display) return raw.display;
  const a = side(raw.a);
  const b = side(raw.b);
  return {
    left: a,
    right: b,
    versions: [a, b],
    rows: raw.items.map((item) => {
      const delta = item.delta === null ? null : Number(item.delta);
      return {
        metric: item.metric_code ?? '',
        left: item.a_value === null ? '—' : number(item.a_value),
        right: item.b_value === null ? '—' : number(item.b_value),
        delta: delta === null ? '—' : number(delta),
        changed: delta !== 0,
        reason: CAUSE[item.cause] ?? item.cause,
        retro_change_id: null,
      };
    }),
  };
}
