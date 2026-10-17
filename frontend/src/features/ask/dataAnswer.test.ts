import { describe, expect, it } from 'vitest';
import {
  drillQuestion,
  figureChart,
  figureDrill,
  figureRun,
  figureTrust,
  readChartSpec,
  readFigures,
  readPlan,
  type FigureWire,
} from './dataAnswer';

const fig = (key: string, dims: Record<string, string>, value: string | null, extra: Partial<FigureWire> = {}): FigureWire => ({
  key,
  label: `Tỷ lệ nợ xấu, ${Object.values(dims).join(', ')}`,
  value,
  unit: '%',
  decimals: 2,
  dimensions: dims,
  display_state: value === null ? 'not_available' : 'shown',
  query_run_id: 'qr-1',
  metric_version_id: 'mv-1',
  trust: 'Theo chỉ số chuẩn',
  ...extra,
});

describe('dataAnswer', () => {
  it('reads only well-formed wire values', () => {
    expect(readFigures(null)).toEqual([]);
    expect(readFigures([1, { key: 'F1' }])).toHaveLength(1);
    // a derived figure (gap, change, share) is in the text, never a row or a bar
    expect(readFigures([{ key: 'F1' }, { key: 'F2', source: { compute_run_id: 'c', expr: 'F1 - F1' } }])).toHaveLength(1);
    expect(readChartSpec({ type: 'pie', x: 'vung' })).toBeNull();
    expect(readChartSpec({ type: 'bar', x: 'vung', y: 'value' })).not.toBeNull();
  });

  it('builds the table from the figures, naming columns from the spec', () => {
    const spec = readChartSpec({ type: 'bar', x: 'vung', x_label: 'Vùng', y: 'value', y_label: 'Tỷ lệ nợ xấu' });
    const run = figureRun([fig('F1', { vung: 'Bắc' }, '1.84'), fig('F2', { vung: 'Nam' }, '2.5')], spec);
    expect(run?.columns).toEqual(['Vùng', 'Tỷ lệ nợ xấu']);
    expect(run?.rows).toEqual([
      { Vùng: 'Bắc', 'Tỷ lệ nợ xấu': 1.84 },
      { Vùng: 'Nam', 'Tỷ lệ nợ xấu': 2.5 },
    ]);
    expect(run?.column_units).toEqual({ 'Tỷ lệ nợ xấu': '%' });
    expect(run?.id).toBe('qr-1');
  });

  it('BR-178: a masked figure says why in the table and is never drawn', () => {
    const spec = readChartSpec({ type: 'bar', x: 'vung', y: 'value', y_label: 'NPL' });
    const figures = [
      fig('F1', { vung: 'Bắc' }, '1.84'),
      fig('F2', { vung: 'Nam' }, null, { display_state: 'masked_stale' }),
      fig('F3', { vung: 'Trung' }, '3'),
    ];
    expect(figureRun(figures, spec)?.rows[1]).toEqual({ vung: 'Nam', NPL: 'Đã ẩn: dữ liệu quá hạn cập nhật' });
    expect(figureChart(figures, spec)?.data.map((d) => d.vung)).toEqual(['Bắc', 'Trung']);
  });

  it('draws a time cut as an ordered line, one line per series member', () => {
    const spec = readChartSpec({ type: 'line', x: 'thang', x_label: 'Tháng', series: 'sp', y: 'value', y_label: 'NPL', unit: '%' });
    const chart = figureChart(
      [
        fig('F1', { thang: '2026-09', sp: 'Thẻ' }, '2'),
        fig('F2', { thang: '2026-08', sp: 'Thẻ' }, '1'),
        fig('F3', { thang: '2026-08', sp: 'Vay' }, '3'),
      ],
      spec,
    );
    expect(chart).toMatchObject({ chart_type: 'line', x_key: 'Tháng', y_keys: ['Thẻ', 'Vay'], title: 'NPL (%) theo Tháng' });
    expect(chart?.data).toEqual([
      { Tháng: '2026-08', Thẻ: 1, Vay: 3 },
      { Tháng: '2026-09', Thẻ: 2 },
    ]);
  });

  it('draws nothing without a spec or with fewer than two points', () => {
    const spec = readChartSpec({ type: 'bar', x: 'vung', y: 'value' });
    expect(figureChart([fig('F1', { vung: 'Bắc' }, '1')], null)).toBeNull();
    expect(figureChart([fig('F1', { vung: 'Bắc' }, '1'), fig('F2', { vung: 'Nam' }, null)], spec)).toBeNull();
  });

  it('keeps the label the server gave, and nothing for one it does not know', () => {
    expect(figureTrust([fig('F1', {}, '1')])).toMatchObject({ label: 'Theo chỉ số chuẩn', level: 'GOVERNED' });
    expect(figureTrust([fig('F1', {}, '1', { trust: 'chưa có định nghĩa chuẩn' })])).toMatchObject({
      label: 'Chưa có định nghĩa chuẩn',
      tone: 'warning',
    });
    expect(figureTrust([fig('F1', {}, '1', { trust: 'khác' })])).toBeNull();
  });

  it('drills a bar chart along the cuts the metric allows, never a line or a series', () => {
    const drill = [{ code: 'chi_nhanh', name: 'chi nhánh' }];
    expect(figureDrill(readChartSpec({ type: 'bar', x: 'vung', x_label: 'Vùng', y: 'value', drill }))).toEqual({
      axis: 'Vùng',
      cuts: drill,
    });
    expect(figureDrill(readChartSpec({ type: 'line', x: 'thang', y: 'value', drill }))).toBeNull();
    expect(figureDrill(readChartSpec({ type: 'bar', x: 'vung', series: 'sp', y: 'value', drill }))).toBeNull();
    expect(drillQuestion('Miền Bắc', 'chi nhánh')).toBe('Đi sâu Miền Bắc theo chi nhánh');
  });

  it('AC-01-04-07 reads one chip per plan chip, inherited ones marked, and the step awaiting a yes', () => {
    const { reading, proposal } = readPlan({
      chips: [
        { kind: 'metric', value: 'NPL_RATIO', label: 'Tỷ lệ nợ xấu', source: 'inherited' },
        { kind: 'filter', value: 'vung=MB', label: 'Miền Bắc', source: 'default' },
        { kind: 'filter', value: 'sp=TD', label: 'Tín dụng', source: 'inherited' },
        { kind: 'option', value: 'DU_NO', label: 'Dư nợ' },
      ],
      steps: {
        steps: [
          { id: 's1', kind: 'semantic_query' },
          {
            id: 's2',
            kind: 'skill',
            checkpoint: 'confirm',
            explain: 'Phân rã biến động',
            args: { category: 'decomposition', params: { metric: 'NPL_RATIO', dimension: 'vung', period: '2026-09', base_period: '2026-08' } },
          },
        ],
      },
    });
    expect(reading.map((c) => [c.label, c.value, c.inherited])).toEqual([
      ['Chỉ số', 'Tỷ lệ nợ xấu', true],
      ['Phạm vi', 'Miền Bắc', false],
      ['Phạm vi', 'Tín dụng', true],
    ]);
    expect(proposal?.params.base_period).toBe('2026-08');
    expect(readPlan({ chips: [], steps: { steps: [{ kind: 'skill', checkpoint: 'none' }] } }).proposal).toBeNull();
  });
});
