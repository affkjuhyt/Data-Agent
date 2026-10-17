import { describe, expect, it } from 'vitest';
import { explanationParams, periodKey, type ExploreModel, type ExploreResult } from './explore';

const dim = (id: string, code: string, is_time: boolean) => ({
  id,
  code,
  label: code,
  group_label: '',
  values: [],
  incompatible_metric_ids: [],
  incompatible_reason: null,
  parent_id: null,
  is_time,
});

const MODEL = {
  metrics: [{ id: 'm-npl', code: 'NPL_RATIO' }],
  dimensions: [dim('d-vung', 'vung', false), dim('d-quy', 'quy', true), dim('d-thang', 'thang', true)],
} as unknown as ExploreModel;

const result = (series: string[]) =>
  ({ category_dimension_id: 'd-vung', series: series.map((key) => ({ key, label: key })), rows: [] }) as unknown as ExploreResult;

describe('Agent giải thích: the decomposition skill’s params from the shelves', () => {
  it('reads the period labels a result carries', () => {
    expect(['2026-09-30', 'T9/2026', '09/2026', 'tháng 9/2026', 'Q3/2026', '2026-Q3', 'Quý 3'].map(periodKey)).toEqual([
      '2026-09',
      '2026-09',
      '2026-09',
      '2026-09',
      '2026-Q3',
      '2026-Q3',
      null,
    ]);
  });

  it('decomposes the last period against the one before, cut by the shelf’s other dimension', () => {
    const input = { metric_ids: ['m-npl'], dimension_ids: ['d-vung', 'd-quy'], filters: [] };
    expect(explanationParams(result(['Q3/2026', 'Q2/2026']), input, MODEL)).toEqual({
      metric: 'NPL_RATIO',
      dimension: 'vung',
      period: '2026-Q3',
      base_period: '2026-Q2',
    });
  });

  it('proposes nothing without a time dimension, two periods, or one grain', () => {
    const noTime = { metric_ids: ['m-npl'], dimension_ids: ['d-vung'], filters: [] };
    expect(explanationParams(result(['Q2/2026', 'Q3/2026']), noTime, MODEL)).toBeNull();
    const input = { metric_ids: ['m-npl'], dimension_ids: ['d-vung', 'd-thang'], filters: [] };
    expect(explanationParams(result(['T9/2026']), input, MODEL)).toBeNull();
    expect(explanationParams(result(['T9/2026', 'Q3/2026']), input, MODEL)).toBeNull();
  });
});
