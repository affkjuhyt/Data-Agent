import { describe, expect, it } from 'vitest';
import {
  readArtifact,
  readExploreResult,
  readQueryRows,
  type ExploreModel,
  type QueryRunWire,
} from './explore';
import type { components } from './schema';

const model: ExploreModel = {
  metrics: [
    {
      id: 'm-npl',
      label: 'Nợ xấu (%)',
      code: 'NPL_RATIO',
      certified: true,
      version: 4,
      unit: 'percent',
      increase_is_bad: true,
      allowed_dimension_ids: ['d-region'],
    },
  ],
  dimensions: [
    {
      id: 'd-region',
      code: 'vung',
      label: 'Vùng',
      group_label: 'Chi nhánh',
      values: [],
      incompatible_metric_ids: [],
      incompatible_reason: null,
      parent_id: null,
      is_time: false,
    },
    {
      id: 'd-branch',
      code: 'chi_nhanh',
      label: 'Chi nhánh',
      group_label: 'Chi nhánh',
      values: [],
      incompatible_metric_ids: [],
      incompatible_reason: null,
      parent_id: 'd-region',
      is_time: false,
    },
    {
      id: 'd-quarter',
      code: 'quy',
      label: 'Quý',
      group_label: 'Thời gian',
      values: [],
      incompatible_metric_ids: [],
      incompatible_reason: null,
      parent_id: null,
      is_time: true,
    },
  ],
};

const run = { id: 'qr-1', data_space_id: 'ds-1', row_count: 4, duration_ms: 800 } as QueryRunWire;

describe('readQueryRows', () => {
  it('reads {name, type} columns and both array and object rows', () => {
    const rows = readQueryRows({
      columns: [{ name: 'vung', type: 'text' }, 'value'],
      items: [['Bắc', '1.5'], { vung: 'Nam', value: 2 }],
      next_page_token: '',
      truncated: false,
      expires_at: '2026-11-01T00:00:00Z',
    });
    expect(rows.columns).toEqual([
      { name: 'vung', type: 'text' },
      { name: 'value', type: null },
    ]);
    expect(rows.rows).toEqual([
      ['Bắc', '1.5'],
      ['Nam', 2],
    ]);
  });
});

describe('readExploreResult', () => {
  it('pivots the long result: first dimension the category, second the series', () => {
    const result = readExploreResult(
      run,
      {
        columns: ['metric', 'vung', 'quy', 'value'].map((name) => ({ name, type: null })),
        rows: [
          ['NPL_RATIO', 'Bắc', 'Q2', '1.5'],
          ['NPL_RATIO', 'Bắc', 'Q3', '2.0'],
          ['NPL_RATIO', 'Nam', 'Q2', 1],
          ['NPL_RATIO', 'Nam', 'Q3', null],
        ],
        truncated: false,
        next_page_token: '',
      },
      { metric_ids: ['m-npl'], dimension_ids: ['d-region', 'd-quarter'], filters: [] },
      model,
    );
    expect(result.title).toBe('Nợ xấu (%) theo vùng · Q2 so với Q3');
    expect(result.subtitle_text).toBe('NPL_RATIO v4 · 0,8 giây');
    expect(result.series.map((s) => s.key)).toEqual(['Q2', 'Q3']);
    expect(result.rows[0]).toMatchObject({ key: 'Bắc', values: [1.5, 2], delta: 0.5 });
    expect(result.rows[1]).toMatchObject({ key: 'Nam', values: [1, null], delta: null });
    expect(result.category_dimension_id).toBe('d-region');
    expect(result.drill_dimension_id).toBe('d-branch');
    expect(result.increase_is_bad).toBe(true);
    // No A3 field carries these: they stay empty rather than invented.
    expect(result.next_steps).toEqual([]);
    expect(result.reconciliation_text).toBeNull();
  });
});

describe('readArtifact', () => {
  it('reads an answer: figures to bars and table, number check, the run behind them', () => {
    const artifact = readArtifact(
      {
        id: 'a-1',
        question_id: 'q-1',
        text: 'Nợ xấu tăng.',
        agent_name: 'Agent Phân tích',
        created_at: 'not a date',
        number_check: { numbers_total: 2, untraceable_final: 0 },
        figures: [
          { label: 'Bắc', value: '0.61', unit: '%', decimals: 2, query_run_id: 'qr-9' },
          { label: 'Nam', value: '-0.04', unit: '%', decimals: 2, display_state: 'shown' },
          { label: 'Ẩn', value: '9', display_state: 'withheld' },
        ],
      } as unknown as components['schemas']['AnswerView'],
      'Nợ xấu tăng ở đâu?',
    );
    expect(artifact.title).toBe('Nợ xấu tăng ở đâu?');
    expect(artifact.checked).toBe(true);
    expect(artifact.meta_text).toBe('Agent Phân tích');
    expect(artifact.table.rows).toEqual([
      ['Bắc', '0,61%'],
      ['Nam', '-0,04%'],
    ]);
    expect(artifact.chart?.bars.map((b) => b.tone)).toEqual(['normal', 'down']);
    expect(artifact.query_run_id).toBe('qr-9');
  });

  it('has no chart and is unchecked without traced figures', () => {
    const artifact = readArtifact(
      {
        id: 'a-2',
        question_id: 'q-2',
        text: '',
        agent_name: null,
        created_at: '2026-10-08T02:58:00Z',
        number_check: {},
        figures: [],
      } as unknown as components['schemas']['AnswerView'],
      null,
    );
    expect(artifact.title).toBe('Câu trả lời');
    expect(artifact.checked).toBe(false);
    expect(artifact.chart).toBeNull();
    expect(artifact.query_run_id).toBeNull();
  });
});
