import { describe, expect, it } from 'vitest';
import { readContextEval, type EvalSetWire } from './semanticLayer';

describe('readContextEval', () => {
  it('adapts an API eval-set detail to the evaluation screen model', () => {
    const view = readContextEval({
      id: 'set-1',
      data_space_id: 'space-1',
      subject_type: 'context',
      subject_id: null,
      name: 'Rủi ro tín dụng',
      pass_threshold: 0.9,
      min_items: 10,
      status: 'active',
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
      item_count: 12,
      latest_run: {
        id: 'run-1',
        eval_set_id: 'set-1',
        subject_version: 'NPL_RATIO v5',
        trigger: 'manual',
        trigger_ref: null,
        item_count: 12,
        passed: 10,
        failed: 2,
        correct_refusals: 1,
        pass_rate: 0.8333,
        baseline_run_id: null,
        regressions: 0,
        status: 'completed',
        started_at: '2026-10-13T00:01:00Z',
        finished_at: '2026-10-13T00:02:00Z',
        created_at: '2026-10-13T00:01:00Z',
      },
      runs: [],
      failures_run_id: null,
      failures: [
        {
          item: {
            id: 'item-1',
            eval_set_id: 'set-1',
            category: 'correct_result',
            input: { question: 'Nợ xấu miền Bắc?' },
            as_of: null,
            expected: { scope: 'Miền Bắc' },
            tolerance: null,
            origin: 'manual',
            origin_ref: null,
            status: 'active',
            created_at: '2026-10-13T00:00:00Z',
            updated_at: '2026-10-13T00:00:00Z',
          },
          result: {
            id: 'result-1',
            eval_item_id: 'item-1',
            understanding_ok: false,
            value_ok: false,
            behavior_ok: true,
            passed: false,
            actual: { scope: '25 tỉnh' },
            cause: 'Thuật ngữ mơ hồ',
            changed_from_baseline: true,
            reviewed_by: null,
            created_at: '2026-10-13T00:02:00Z',
          },
        },
      ],
    } as EvalSetWire);

    expect(view.data_space_name).toBe('Rủi ro tín dụng');
    expect(view.kpis[1].value).toBe('10 / 12');
    expect(view.misses[0]).toEqual({
      question: '{"question":"Nợ xấu miền Bắc?"}',
      expected: '{"scope":"Miền Bắc"}',
      understood: '{"scope":"25 tỉnh"}',
      cause: 'Thuật ngữ mơ hồ',
    });
  });
});
