/**
 * The receipt and its re-run as the api sends them (ReceiptView, ReplayView), read into what
 * "P01 · Biên nhận câu trả lời" draws: masked and missing figures, frontier models, mismatches.
 */
import { describe, expect, it } from 'vitest';
import { readReceipt, readReplay, type ReceiptWire, type ReplayWire } from './answerReceipts';

const figure = (over: object) => ({
  id: 'rf-1',
  figure_key: 'F1',
  label: 'Giá trần E5RON92',
  value: '19730',
  unit: 'đ/lít',
  display_state: 'shown',
  freshness_state: 'fresh',
  stale_note: null,
  source_kind: 'query_run',
  query_run_id: 'qr-1',
  compute_run_id: null,
  published_result_id: null,
  metric_version_id: 'mv-2',
  metric_version_no: 2,
  metric_certified_at: '2026-06-15T09:00:00Z',
  compiled_sql_sha256: 'abc',
  expr_sha256: null,
  dataset_versions: [{ dataset_id: 'ds-1', version: 41, as_of: '2026-07-02' }],
  as_of: '2026-07-02',
  due_at: null,
  ...over,
});

const receipt = {
  id: 'rc-1',
  answer_id: 'a-1',
  receipt_no: 'BN-20260702-000184',
  data_product_id: 'dp-1',
  data_product_code: 'gia_xang',
  data_product_version: 3,
  answered_at: '2026-07-02T16:05:00Z',
  compiler_version: 'semcomp 1.8.2',
  sealed_at: '2026-07-02T16:05:00Z',
  receipt_sha256: 'f'.repeat(64),
  frontier_used: true,
  models: [
    { task: 'plan', provider_kind: 'on_prem', provider_display_name: 'vLLM', model_name: 'Qwen' },
    {
      task: 'answer',
      provider_kind: 'frontier',
      provider_display_name: 'A (API)',
      model_name: 'L',
    },
    {
      task: 'answer',
      provider_kind: 'frontier',
      provider_display_name: 'A (API)',
      model_name: 'L',
    },
  ],
  figures: [
    figure({}),
    figure({
      figure_key: 'F2',
      value: null,
      display_state: 'masked_stale',
      freshness_state: 'overdue',
      stale_note: 'dữ liệu đến 25/06/2026, đã quá hạn cập nhật',
    }),
    figure({
      figure_key: 'F3',
      source_kind: 'compute_run',
      compute_run_id: 'cr-1',
      expr_sha256: 'e',
    }),
    figure({ figure_key: 'F4', freshness_state: 'not_applicable' }),
  ],
} as unknown as ReceiptWire;

describe('answer receipts from the api', () => {
  it('reads figures, their sources and the models of the turn', () => {
    const r = readReceipt(receipt);
    expect(r).toMatchObject({
      id: 'rc-1',
      code: 'BN-20260702-000184',
      status: 'locked',
      data_product: { id: 'dp-1', name: 'gia_xang v3' },
      frontier_used: true,
    });
    expect(r.models).toEqual([
      { name: 'Qwen', provider_label: 'vLLM', external: false },
      { name: 'L', provider_label: 'A (API)', external: true },
    ]);
    expect(r.figures.map((f) => [f.key, f.value_text, f.state])).toEqual([
      ['F1', '19730 đ/lít', 'fresh'],
      ['F2', null, 'stale'],
      ['F3', '19730 đ/lít', 'fresh'],
      ['F4', '19730 đ/lít', 'not_applicable'],
    ]);
    expect(r.figures[0].source).toEqual({
      kind: 'query_run',
      query_run_id: 'qr-1',
      metric: { version: 2, certified_at: '2026-06-15T09:00:00Z' },
      datasets: [{ version: 41, as_of: '2026-07-02' }],
      query: { sql_sha256: 'abc', compiler: 'semcomp 1.8.2' },
    });
    expect(r.figures[2].source).toEqual({
      kind: 'compute_run',
      compute_run_id: 'cr-1',
      expr_sha256: 'e',
    });
  });

  it('reads an unsealed receipt as open', () => {
    expect(readReceipt({ ...receipt, sealed_at: null, receipt_sha256: null }).status).toBe('open');
  });

  it('counts the figures a re-run reproduced and names the ones that moved', () => {
    const run = readReplay({
      id: 'rr-1',
      mode: 'figures',
      status: 'completed',
      outcome: 'differs_explained',
      cause_summary: 'Dữ liệu kỳ 25/06 được sửa hồi tố',
      started_at: '2026-07-03T09:00:00Z',
      finished_at: '2026-07-03T09:00:05Z',
      replay_text: null,
      figure_results: [
        {
          figure_key: 'F1',
          original_value: '19730',
          replay_value: '19730',
          sql_sha256_match: true,
          result_sha256_match: true,
        },
        {
          figure_key: 'F2',
          original_value: '1055',
          replay_value: '1060',
          sql_sha256_match: true,
          result_sha256_match: false,
        },
      ],
    } as unknown as ReplayWire);
    expect(run).toMatchObject({
      ran_at: '2026-07-03T09:00:05Z',
      failed: false,
      figures_total: 2,
      figures_matched: 1,
      values_text: ['19730', '1060'],
      mismatches: [{ key: 'F2', before: '1055', after: '1060' }],
      hashes_match: false,
      cause_summary: 'Dữ liệu kỳ 25/06 được sửa hồi tố',
    });
  });
});
