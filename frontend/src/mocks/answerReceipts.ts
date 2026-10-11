import { http, HttpResponse } from 'msw';
import type { AnswerReceipt, ReceiptRerun, RerunMode } from '../lib/v1/answerReceipts';

/*
 * MSW handlers for lib/v1/answerReceipts.ts (PROPOSAL endpoints, UC-01-07). The receipt of
 * "P01 · Biên nhận câu trả lời" (2420:15098): the E5RON92 ceiling price of 02/07/2026 and its
 * change; a second answer shows a figure past its update window, a missing one and an external
 * model (the frame's "Biến thể số" and "Biến thể frontier"); a third is over a Data Space the
 * viewer is not in. Ids the Ask screen invents on the client have no receipt (404).
 */
const problem = (status: number, code: string, detail: string, extra: object = {}) =>
  HttpResponse.json(
    { title: code, status, code, detail, ...extra },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const hex = (head: string, tail: string, fill: string) =>
  head + fill.repeat(64).slice(0, 64 - head.length - tail.length) + tail;

function receiptsFixture(): AnswerReceipt[] {
  // The P07 context data product (mocks of the other session use the adp- prefix).
  const xangDau = { id: 'adp-fuel-prices', name: 'Giá xăng dầu trong nước v3' };
  const metric = {
    code: 'gia_tran_ban_le',
    version: 2,
    signed_by_name: 'Nguyễn Thị Hạnh',
    signed_at: '2026-06-15',
  };
  const query = { sql_sha256: hex('9f3c', 'e1', '7a'), compiler: 'semcomp 1.8.2' };
  return [
    {
      answer_id: 'answer-e5ron92-0207',
      code: 'BN-20260702-000184',
      status: 'locked',
      created_at: '2026-07-02T16:05:00',
      data_product: xangDau,
      asked_by_name: 'Trần Văn Khánh',
      receipt_sha256: hex('4b7e91c2', 'a90c', '3d'),
      figures: [
        {
          key: 'F1',
          value_text: '19.730 đ/lít',
          state: 'fresh',
          source: {
            kind: 'query_run',
            query_run_id: 'qr-20260702-0412',
            metric,
            dataset: { code: 'gia_xang_dau_ky_dieu_hanh', version: 41, as_of: '2026-07-02' },
            query,
            freshness: {
              loaded_at: '2026-07-02T15:12:00',
              due_at: '2026-07-02T16:30:00',
              data_until: '2026-07-02',
            },
          },
        },
        {
          key: 'F2',
          value_text: '1.055 đ/lít',
          state: 'fresh',
          source: {
            kind: 'compute_run',
            compute_run_id: 'cr-20260702-0087',
            description: 'hiệu hai kỳ 25/06 và 02/07',
            inputs_text: 'F1 và giá trần kỳ 25/06/2026 = 20.785 đ/lít',
          },
        },
      ],
      model: {
        name: 'Qwen3.6-27B',
        provider_label: 'vLLM tại chỗ',
        external: false,
        external_scope_text: null,
      },
      last_rerun: {
        id: 'rr-000184-1',
        mode: 'figures',
        ran_at: '2026-07-02T16:20:00',
        figures_total: 2,
        figures_matched: 2,
        values_text: ['19.730', '1.055'],
        mismatches: [],
        hashes_match: true,
        answer_text: null,
      },
    },
    {
      answer_id: 'answer-e5ron95-0207',
      code: 'BN-20260702-000191',
      status: 'locked',
      created_at: '2026-07-02T16:42:00',
      data_product: xangDau,
      asked_by_name: 'Trần Văn Khánh',
      receipt_sha256: hex('c03a5e17', '5b21', '8e'),
      figures: [
        {
          key: 'F1',
          value_text: '20.810 đ/lít',
          state: 'stale',
          source: {
            kind: 'query_run',
            query_run_id: 'qr-20260702-0433',
            metric,
            dataset: { code: 'gia_xang_dau_ky_dieu_hanh', version: 40, as_of: '2026-06-25' },
            query: { sql_sha256: hex('51d0', '6c', '2f'), compiler: 'semcomp 1.8.2' },
            freshness: {
              loaded_at: '2026-06-25T15:08:00',
              due_at: '2026-07-02T16:30:00',
              data_until: '2026-06-25',
            },
          },
        },
        {
          key: 'F2',
          value_text: null,
          state: 'missing',
          source: {
            kind: 'query_run',
            query_run_id: 'qr-20260702-0434',
            metric,
            dataset: { code: 'gia_xang_dau_ky_dieu_hanh', version: 40, as_of: '2026-06-25' },
            query: { sql_sha256: hex('e8a4', '07', '91'), compiler: 'semcomp 1.8.2' },
            freshness: {
              loaded_at: '2026-06-25T15:08:00',
              due_at: '2026-07-09T16:30:00',
              data_until: null,
            },
          },
        },
      ],
      model: {
        name: 'Frontier-L',
        provider_label: 'Nhà cung cấp A (API)',
        external: true,
        external_scope_text: 'chỉ với dữ liệu công khai',
      },
      last_rerun: null,
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
  let reruns = 0;

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
    http.post('*/v1/answers/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'rerun') return undefined;
      const found = receipts.find((r) => r.answer_id === id);
      if (!found) return problem(404, 'not_found', 'Câu trả lời này chưa có biên nhận.');
      const body = (await request.json()) as { mode?: RerunMode };
      if (body?.mode !== 'figures' && body?.mode !== 'figures_and_text')
        return problem(422, 'validation_error', 'Chọn chạy lại chỉ số hay cả câu chữ.');
      // At the receipt's as-of the same SQL over the same dataset version returns the same rows.
      const valued = found.figures.filter((f) => f.value_text !== null);
      const rerun: ReceiptRerun = {
        id: `rr-${found.code}-${++reruns}`,
        mode: body.mode,
        ran_at: new Date().toISOString(),
        figures_total: valued.length,
        figures_matched: valued.length,
        values_text: valued.map((f) => (f.value_text ?? '').replace(/\s.*$/, '')),
        mismatches: [],
        hashes_match: true,
        answer_text: body.mode === 'figures_and_text' ? (REWRITTEN[found.answer_id] ?? null) : null,
      };
      found.last_rerun = rerun;
      return HttpResponse.json(rerun);
    }),
  ];
}
