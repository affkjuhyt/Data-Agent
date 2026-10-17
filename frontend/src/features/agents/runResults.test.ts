/**
 * A run's query step as a chart: the rows the worker kept (runs.py, at most 50) drawn along the
 * step's chart_spec, the same way the Ask screen draws an answer.
 */
import { describe, expect, it } from 'vitest';
import { stepChart } from './runResults';

const byRegion = {
  columns: ['vung', 'value'],
  rows: [
    ['Miền Bắc', 2.37],
    ['Miền Nam', 1.26],
  ],
  chart_spec: { type: 'bar', x: 'vung', x_label: 'Vùng', y_label: 'Tỷ lệ nợ xấu', unit: '%' },
};

describe('stepChart', () => {
  it('draws a member cut as bars, labelled in words', () => {
    expect(stepChart(byRegion)).toEqual({
      chart_type: 'bar',
      title: 'Tỷ lệ nợ xấu (%) theo Vùng',
      x_key: 'Vùng',
      y_keys: ['Tỷ lệ nợ xấu'],
      data: [
        { Vùng: 'Miền Bắc', 'Tỷ lệ nợ xấu': 2.37 },
        { Vùng: 'Miền Nam', 'Tỷ lệ nợ xấu': 1.26 },
      ],
    });
  });

  it('puts a time cut in order', () => {
    const chart = stepChart({
      columns: ['ngay', 'value'],
      rows: [
        ['2026-09-30', '1.83'],
        ['2026-04-30', '1.46'],
      ],
      chart_spec: { type: 'line', x: 'ngay', x_label: 'Ngày', y_label: 'Tỷ lệ nợ xấu', unit: '%' },
    });
    expect(chart?.chart_type).toBe('line');
    expect(chart?.data.map((p) => p['Ngày'])).toEqual(['2026-04-30', '2026-09-30']);
    expect(chart?.data[0]['Tỷ lệ nợ xấu']).toBe(1.46);
  });

  it('draws a second cut as one series per member', () => {
    const chart = stepChart({
      columns: ['vung', 'san_pham', 'value'],
      rows: [
        ['Miền Bắc', 'Thẻ', 3],
        ['Miền Bắc', 'Vay', 2],
        ['Miền Nam', 'Thẻ', 1],
      ],
      chart_spec: { type: 'bar', x: 'vung', x_label: 'Vùng', series: 'san_pham', y_label: 'NPL' },
    });
    expect(chart?.y_keys).toEqual(['Thẻ', 'Vay']);
    expect(chart?.data).toEqual([
      { Vùng: 'Miền Bắc', Thẻ: 3, Vay: 2 },
      { Vùng: 'Miền Nam', Thẻ: 1 },
    ]);
  });

  it('draws nothing without a spec, with one point, or from a value that is not a number', () => {
    expect(stepChart({ ...byRegion, chart_spec: null })).toBeNull();
    expect(stepChart({ ...byRegion, rows: [['Miền Bắc', 2.37]] })).toBeNull();
    expect(
      stepChart({
        ...byRegion,
        rows: [
          ['Miền Bắc', null],
          ['Miền Nam', 'x'],
        ],
      }),
    ).toBeNull();
    expect(stepChart(null)).toBeNull();
  });
});
