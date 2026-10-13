import { describe, expect, it } from 'vitest';
import { readDataMart, type DataMartWire } from './dataProducts';

describe('readDataMart', () => {
  it('adapts the API mart row into the star-model screen shape', () => {
    const mart = readDataMart(
      {
        id: 'mart-1',
        data_space_id: 'space-1',
        name: 'credit_risk',
        fact_dataset_id: 'dataset-1',
        grain: 'one loan per day',
        measures: [{ name: 'outstanding_amt' }, { name: 'overdue_amt' }],
        dimensions: [{ name: 'branch', columns: ['branch_id', 'branch_name'] }],
        relations: [],
        checks: [],
        suggested_dimensions: null,
        relation_check_passed: true,
        status: 'draft',
        created_at: '2026-10-13T00:00:00Z',
        updated_at: '2026-10-13T00:00:00Z',
      } as DataMartWire,
      new Map([['dataset-1', 'mart.credit_risk_fact']]),
    );

    expect(mart.name).toBe('credit_risk');
    expect(mart.fact).toEqual({ name: 'mart.credit_risk_fact', lines: ['one loan per day'] });
    expect(mart.dimensions).toEqual([
      { name: 'branch', lines: ['branch_id', 'branch_name'], suggested: false },
    ]);
    expect(mart.product_draft.version).toBe('1');
    expect(mart.product_draft.quality_text).toBe('Đã kiểm tra liên kết');
    expect(mart.suggestion).toBeNull();
  });
});
