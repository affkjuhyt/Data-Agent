import { http, HttpResponse } from 'msw';
import type {
  ReceiptFigureWire,
  ReceiptWire,
  ReplayWire,
  RerunMode,
} from '../lib/v1/answerReceipts';

/*
 * MSW handlers for lib/v1/answerReceipts.ts (A2, UC-01-07), in the api's shapes (ReceiptView,
 * ReplayView). The receipt of "P01 · Biên nhận câu trả lời" (2420:15098): the E5RON92 ceiling
 * price of 02/07/2026 and its change; a second answer shows a figure past its update window, a
 * missing one and an external model (the frame's "Biến thể số" and "Biến thể frontier"); a third
 * is over a Data Space the viewer is not in. Ids the Ask screen invents on the client have no
 * receipt (404).
 */
const problem = (status: number, code: string, detail: string, extra: object = {}) =>
  HttpResponse.json(
    { title: code, status, code, detail, ...extra },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const hex = (head: string, tail: string, fill: string) =>
  head + fill.repeat(64).slice(0, 64 - head.length - tail.length) + tail;

const AT = '2026-07-02T16:05:00+07:00';

function figure(
  key: string,
  over: Partial<ReceiptFigureWire> & Pick<ReceiptFigureWire, 'label' | 'source_kind'>,
): ReceiptFigureWire {
  return {
    id: `rf-${key}-${over.label.length}`,
    figure_key: key,
    value: null,
    unit: 'đ/lít',
    display_state: 'shown',
    stale_note: null,
    requested_period: '2026-07-02',
    metric_id: null,
    metric_version_id: null,
    metric_version_no: null,
    metric_certified_by: null,
    metric_certified_at: null,
    query_run_id: null,
    compute_run_id: null,
    published_result_id: null,
    compiled_sql_sha256: null,
    expr_sha256: null,
    result_sha256: null,
    dataset_versions: [],
    as_of: null,
    freshness_state: 'fresh',
    due_at: null,
    created_at: AT,
    ...over,
  };
}

// The certified version of gia_tran_ban_le (v2, signed 15/06/2026) the query figures read.
const metric = {
  metric_id: 'metric-gia-tran',
  metric_version_id: 'mv-gia-tran-v2',
  metric_version_no: 2,
  metric_certified_by: 'u-hanh',
  metric_certified_at: '2026-06-15T09:00:00+07:00',
};

function receiptsFixture(): ReceiptWire[] {
  const base = {
    data_product_id: 'adp-fuel-prices',
    data_product_code: 'gia_xang_dau_trong_nuoc',
    data_product_version: 3,
    knowledge_mode: 'latest',
    knowledge_at: null,
    compiler_version: 'semcomp 1.8.2',
    runtime_version: 'runtime 3.4.0',
    freshness: [],
    retain_until: '2036-07-02',
  };
  return [
    {
      ...base,
      id: 'rc-000184',
      answer_id: 'answer-e5ron92-0207',
      receipt_no: 'BN-20260702-000184',
      plan_id: 'plan-000184',
      answered_at: AT,
      created_at: AT,
      sealed_at: AT,
      receipt_sha256: hex('4b7e91c2', 'a90c', '3d'),
      models: [
        {
          task: 'answer',
          provider_code: 'vllm-local',
          provider_kind: 'on_prem',
          provider_display_name: 'vLLM tại chỗ',
          model_name: 'Qwen3.6-27B',
        },
      ],
      frontier_used: false,
      figure_count: 2,
      masked_count: 0,
      figures: [
        figure('F1', {
          ...metric,
          label: 'Giá trần E5RON92 kỳ 02/07/2026',
          value: '19.730',
          source_kind: 'query_run',
          query_run_id: 'qr-20260702-0412',
          compiled_sql_sha256: hex('9f3c', 'e1', '7a'),
          dataset_versions: [
            {
              dataset_id: 'ds-gia-xang',
              dataset_version_id: 'dv-41',
              version: 41,
              as_of: '2026-07-02',
            },
          ],
          as_of: '2026-07-02',
          due_at: '2026-07-02T16:30:00+07:00',
        }),
        figure('F2', {
          label: 'Chênh lệch giá trần E5RON92 so với kỳ 25/06/2026',
          value: '1.055',
          source_kind: 'compute_run',
          compute_run_id: 'cr-20260702-0087',
          expr_sha256: hex('a71d', '0c', '5e'),
          freshness_state: 'not_applicable',
        }),
      ],
    },
    {
      ...base,
      id: 'rc-000191',
      answer_id: 'answer-e5ron95-0207',
      receipt_no: 'BN-20260702-000191',
      plan_id: 'plan-000191',
      answered_at: '2026-07-02T16:42:00+07:00',
      created_at: '2026-07-02T16:42:00+07:00',
      sealed_at: '2026-07-02T16:42:00+07:00',
      receipt_sha256: hex('c03a5e17', '5b21', '8e'),
      models: [
        {
          task: 'answer',
          provider_code: 'provider-a',
          provider_kind: 'frontier',
          provider_display_name: 'Nhà cung cấp A (API)',
          model_name: 'Frontier-L',
        },
      ],
      frontier_used: true,
      figure_count: 2,
      masked_count: 1,
      figures: [
        figure('F1', {
          ...metric,
          label: 'Giá trần E5RON95 kỳ 02/07/2026',
          display_state: 'masked_stale',
          freshness_state: 'overdue',
          stale_note: 'dữ liệu đến 25/06/2026, đã quá hạn cập nhật (hạn 02/07 16:30)',
          source_kind: 'query_run',
          query_run_id: 'qr-20260702-0433',
          compiled_sql_sha256: hex('51d0', '6c', '2f'),
          dataset_versions: [
            {
              dataset_id: 'ds-gia-xang',
              dataset_version_id: 'dv-40',
              version: 40,
              as_of: '2026-06-25',
            },
          ],
          as_of: '2026-06-25',
          due_at: '2026-07-02T16:30:00+07:00',
        }),
        figure('F2', {
          ...metric,
          label: 'Giá trần E5RON95 kỳ 09/07/2026',
          display_state: 'not_available',
          freshness_state: 'missing',
          requested_period: '2026-07-09',
          source_kind: 'query_run',
          query_run_id: 'qr-20260702-0434',
          compiled_sql_sha256: hex('e8a4', '07', '91'),
          due_at: '2026-07-09T16:30:00+07:00',
        }),
      ],
    },
  ];
}

const RESTRICTED = 'answer-provision-q3';

// What "Chỉ số và câu chữ" writes back: the same figures in different words.
const REWRITTEN: Record<string, string> = {
  'answer-e5ron92-0207':
    'Kỳ điều hành 02/07/2026, giá trần xăng E5RON92 ở mức 19.730 đ/lít, thấp hơn 1.055 đ/lít so với kỳ 25/06/2026; giá có hiệu lực từ 16:00 ngày 02/07 theo Bộ Công Thương.',
};

export function createAnswerReceiptHandlers() {
  const receipts = receiptsFixture();
  const replays = new Map<string, ReplayWire>();
  let count = 0;

  return [
    http.get('*/v1/answers/:id/receipt', ({ params }) => {
      if (params.id === RESTRICTED)
        return problem(403, 'forbidden', 'Bạn chưa có quyền với Data Space Kế toán.', {
          access: {
            title: 'Bạn chưa có quyền xem biên nhận này',
            detail:
              'Câu trả lời dùng bảng provision_detail thuộc Data Space Kế toán. Chủ sở hữu: Trần Quốc An.',
            data_space_id: 'ds-accounting',
            owner_name: 'Trần Quốc An',
            options: [{ capability: 'view_aggregate', label: 'Xem tổng hợp theo vùng' }],
            reason_hint: 'Kiểm tra biên nhận câu trả lời về trích lập dự phòng Q3',
            fallback_text: null,
          },
        });
      const found = receipts.find((r) => r.answer_id === params.id);
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Câu trả lời này chưa có biên nhận.');
    }),
    http.post('*/v1/receipts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'replay') return undefined;
      const found = receipts.find((r) => r.id === id || r.receipt_no === id);
      if (!found) return problem(404, 'not_found', 'Receipt not found.');
      const body = (await request.json()) as { mode?: RerunMode };
      const mode = body?.mode ?? 'figures';
      // At the receipt's as-of the same SQL over the same dataset version returns the same rows.
      const valued = (found.figures ?? []).filter((f) => f.value !== null);
      const replay: ReplayWire = {
        id: `rr-${found.receipt_no}-${++count}`,
        receipt_id: found.id,
        requested_by: 'u-khanh',
        reason: null,
        mode,
        compiler_version_used: found.compiler_version,
        status: 'completed',
        outcome: 'identical',
        figure_results: valued.map((f) => ({
          figure_key: f.figure_key,
          original_value: f.value,
          replay_value: f.value,
          sql_sha256_match: true,
          result_sha256_match: true,
          cause: null,
        })),
        differs_count: 0,
        cause_summary: null,
        retro_change_ids: [],
        replay_text: mode === 'figures_and_text' ? (REWRITTEN[found.answer_id] ?? null) : null,
        replay_model: mode === 'figures_and_text' ? 'Qwen3.6-27B' : null,
        query_run_ids: [],
        error_book_entry_id: null,
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
      };
      replays.set(replay.id, replay);
      return HttpResponse.json(
        { id: replay.id, status: 'running', poll_after_ms: 2000 },
        { status: 202, headers: { Location: `/v1/receipt-replays/${replay.id}` } },
      );
    }),
    http.get('*/v1/receipt-replays/:id', ({ params }) => {
      const replay = replays.get(String(params.id));
      return replay ? HttpResponse.json(replay) : problem(404, 'not_found', 'Replay not found.');
    }),
  ];
}
