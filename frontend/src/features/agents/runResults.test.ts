/**
 * A run's query step as a chart: the rows the worker kept (runs.py, at most 50) drawn along the
 * step's chart_spec, the same way the Ask screen draws an answer.
 */
import { describe, expect, it } from 'vitest';
import { resultParts, stepChart } from './runResults';

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

describe('resultParts', () => {
  const base = {
    duration_ms: 1,
    parent_seq: null,
    started_at: null,
    actor: '',
    metric_text: null,
    run_as_text: null,
    rows_text: null,
    sql: null,
  };

  it('takes each answered question, and each part of an audit after its summary', () => {
    // Steps as the api sends them, with fields resultParts does not read.
    const steps = [
      {
        ...base,
        seq: 1,
        kind: 'plan',
        name: 'plan',
        summary: 'Kế hoạch',
        status: 'ok',
        output: {},
      },
      {
        ...base,
        seq: 2,
        kind: 'query',
        name: 'query.semantic',
        summary: 'NPL 1,83%.',
        status: 'ok',
        output: { question: 'Tỷ lệ nợ xấu' },
      },
      {
        ...base,
        seq: 3,
        kind: 'query',
        name: 'query.semantic',
        summary: 'Lỗi',
        status: 'retried',
        output: {},
      },
      {
        ...base,
        seq: 4,
        kind: 'tool_call',
        name: 'quality.status',
        summary: 'Kiểm toán Khoản vay (16 dòng): 1 vấn đề cần xử lý.',
        status: 'ok',
        output: {
          parts: [{ question: 'Phân bố Giới tính', summary: 'Nam 55%; Nữ 45%.', ...byRegion }],
        },
      },
    ];
    const parts = resultParts(steps);
    expect(parts.map((p) => [p.question, p.summary, Boolean(p.chart)])).toEqual([
      ['Tỷ lệ nợ xấu', 'NPL 1,83%.', false],
      [null, 'Kiểm toán Khoản vay (16 dòng): 1 vấn đề cần xử lý.', false],
      ['Phân bố Giới tính', 'Nam 55%; Nữ 45%.', true],
    ]);
  });
});
