import { describe, expect, it } from 'vitest';
import { catalogAssets, restApis } from './catalog';
import type { Dataset } from './types';

const dataset = (over: Partial<Dataset>): Dataset =>
  ({
    id: 'dataset-1',
    display_name: 'Bán qua Internet',
    schema_name: 'public',
    table_name: 'fact_internet_sales',
    origin: 'derived',
    source_id: null,
    ...over,
  }) as Dataset;

describe('catalogAssets', () => {
  it('lists a table, a document and a series, each opening its own screen', () => {
    const rows = catalogAssets(
      [dataset({})],
      [
        {
          id: 'doc-1',
          title: 'Định nghĩa chỉ số',
          file_name: 'AW-KPI-01.html',
          doc_no: 'AW-KPI-01',
          collection_id: 'col-1',
          document_source_id: 'src-doc',
        },
      ],
      [{ id: 'ledger~fm~e5', label: 'Xăng E5' }],
    );
    expect(rows.map((row) => [row.kind, row.name, row.open.page])).toEqual([
      ['table', 'Bán qua Internet', 'TABLE'],
      ['document', 'Định nghĩa chỉ số', 'DOCUMENT'],
      ['series', 'Xăng E5', 'FORECAST_LEDGER'],
    ]);
    expect(rows[0].sourceLink).toBeNull();
    expect(rows[1].sourceLink).toEqual({ area: 'data', page: 'COLLECTION', id: 'col-1' });
    expect(rows[2].source).toBe('Sổ dự báo');
  });

  it('points a connected table at its source', () => {
    const [row] = catalogAssets(
      [
        dataset({
          source_id: 'source-1',
          origin: 'source',
          schema_name: 'public',
          table_name: 'fact_reseller_sales',
        }),
      ],
      [],
      [],
    );
    expect(row.source).toBe('public.fact_reseller_sales');
    expect(row.sourceLink).toEqual({ area: 'data', page: 'SOURCE', id: 'source-1' });
  });

  it('lists a column under its table and opens that table', () => {
    const rows = catalogAssets(
      [dataset({ source_id: 'source-1', origin: 'source' })],
      [],
      [],
      [],
      new Map([['dataset-1', [{ id: 'col-order-date', name: 'order_date', display_name: 'Ngày đặt hàng' }]]]),
    );
    const column = rows.find((row) => row.kind === 'column');
    expect(column).toMatchObject({
      id: 'col-order-date',
      name: 'Ngày đặt hàng',
      source: 'public.fact_internet_sales',
      open: { area: 'data', page: 'TABLE', id: 'dataset-1' },
      sourceLink: { area: 'data', page: 'SOURCE', id: 'source-1' },
    });
  });

  it('lists a REST API and leaves a database connection out', () => {
    expect(
      restApis([
        { id: 'api-1', name: 'Tỷ giá', kind: 'rest_api', host: 'api.example' },
        { id: 'db-1', name: 'Kho', kind: 'postgresql', host: 'db.internal' },
      ]),
    ).toEqual([{ id: 'api-1', name: 'Tỷ giá', endpoint: 'api.example' }]);
    const [row] = catalogAssets(
      [],
      [],
      [],
      [{ id: 'api-1', name: 'Tỷ giá', endpoint: 'api.example' }],
    );
    expect(row.kind).toBe('api');
    expect(row.open).toEqual({ area: 'data', page: 'SOURCE', id: 'api-1' });
    expect(row.sourceLink).toBeNull();
  });
});
