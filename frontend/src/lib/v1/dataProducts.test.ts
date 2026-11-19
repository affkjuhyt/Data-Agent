import { describe, expect, it } from 'vitest';
import { martContract, measureColumns, readDataMart, type DataMartWire } from './dataProducts';

const column = (name: string, normalized_type: string, is_key = false) =>
  ({ name, normalized_type, is_key }) as Parameters<typeof measureColumns>[0][number];

describe('a mart from the fact table it is built on', () => {
  const columns = [
    column('order_key', 'integer', true),
    column('product_key', 'integer'),
    column('sales_amount', 'decimal'),
    column('order_qty', 'integer'),
    column('currency', 'string'),
  ];

  it('offers the fact’s number columns that are not keys as measures', () => {
    expect(measureColumns(columns)).toEqual(['sales_amount', 'order_qty']);
  });

  it('commits the product to the mart’s dimension keys and measures, typed as the fact has them', () => {
    const mart = {
      dimensions: [{ name: 'dim_product', key: 'product_key' }],
      measures: [{ name: 'sales_amount', agg: 'sum' }],
    } as DataMartWire;
    expect(martContract(mart, columns)).toEqual({
      columns: [
        { name: 'product_key', type: 'integer' },
        { name: 'sales_amount', type: 'decimal' },
      ],
    });
  });
});

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
    expect(mart.fact).toEqual({
      name: 'mart.credit_risk_fact',
      lines: ['one loan per day', 'outstanding_amt', 'overdue_amt'],
    });
    expect(mart.dimensions).toEqual([
      { name: 'branch', lines: ['branch_id', 'branch_name'], suggested: false },
    ]);
    expect(mart.product_draft.version).toBe('1');
    expect(mart.product_draft.quality_text).toBe('Đã kiểm tra liên kết');
    expect(mart.suggestion).toBeNull();
  });
});
