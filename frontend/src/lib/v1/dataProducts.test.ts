import { describe, expect, it } from 'vitest';
import {
  martContract,
  measureColumns,
  readDataMart,
  readPublishedDetail,
  readPublishedProducts,
  type DataMartWire,
  type PublishedProductWire,
} from './dataProducts';

const product = (over: Partial<PublishedProductWire>): PublishedProductWire => ({
  id: 'p-2',
  data_space_id: 'ds-1',
  layer: 'published',
  code: 'doanh_thu_vung',
  name: 'Doanh thu theo vùng',
  version: 2,
  dataset_id: null,
  data_mart_id: 'mart-1',
  contract: { columns: [{ name: 'region_key', type: 'integer' }] },
  consumers: [],
  status: 'published',
  breaking_notice_at: null,
  retire_after: null,
  approved_by: null,
  owner_id: 'u-1',
  created_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-02T00:00:00Z',
  ...over,
});

describe('the published products of a Data Space (GET /v1/data-products?layer=published)', () => {
  const rows = [
    product({
      id: 'p-3',
      version: 3,
      status: 'draft',
      breaking_notice_at: '2026-11-03T00:00:00Z',
      contract: { columns: [{ name: 'region_key', type: 'text' }] },
    }),
    product({ consumers: ['ds-2', 'ds-3'] }),
    product({ id: 'p-1', version: 1, status: 'deprecated' }),
  ];

  it('lists each product once, at the version it serves, with the draft above it', () => {
    expect(readPublishedProducts(rows)).toEqual([
      {
        id: 'p-2',
        name: 'Doanh thu theo vùng',
        code: 'doanh_thu_vung',
        version: '2',
        next_version: '3',
        description: '',
        status: 'published',
        breaking_change: true,
        used_by_spaces: 2,
        quality_pct: null,
        refreshed_at: '2026-11-02T00:00:00Z',
      },
    ]);
  });

  it('opens a product with its contract, its versions and who reads it', () => {
    const detail = readPublishedDetail(rows, 'p-2', new Map([['ds-2', 'Bán lẻ']]));
    expect(detail).toMatchObject({
      contract: [{ name: 'region_key', type: 'integer', description: '', changed: true }],
      consumers: [
        { data_space_name: 'Bán lẻ', used_for: '', version: '2' },
        { data_space_name: 'Data Space khác', used_for: '', version: '2' },
      ],
      consumer_spaces_total: 2,
      affected_by_next: 2,
      versions: [
        { version: '3', change: 'Thay đổi phá vỡ hợp đồng', state: 'draft' },
        { version: '2', change: '', state: 'current' },
        { version: '1', change: '', state: 'supported' },
      ],
      checks: [],
      agent_note: null,
    });
  });
});

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
