import { describe, expect, it } from 'vitest';
import { readExploreResult, readQuestion, type ExploreModel } from './explore';

const metric = (id: string, label: string) => ({ id, label, code: id, certified: true, allowed_dimension_ids: [], unit: 'amount', increase_is_bad: false, version: 1 });
const dim = (id: string, label: string, is_time = false) => ({ id, label, code: id, is_time, incompatible_metric_ids: [], parent_id: null });

const model = {
  metrics: [metric('dt', 'Doanh thu'), metric('dti', 'Doanh thu Internet'), metric('sdi', 'Số đơn Internet')],
  dimensions: [
    dim('vung', 'Vùng bán hàng'),
    dim('sp', 'Sản phẩm'),
    dim('nsp', 'Nhóm sản phẩm'),
    dim('ngay', 'Ngày đặt hàng (Internet)', true),
    dim('ntc', 'Năm tài chính'),
  ],
} as unknown as ExploreModel;

describe('readQuestion (K2: Hỏi bằng lời, by rule)', () => {
  it.each([
    ['doanh thu internet theo nhóm sản phẩm', ['dti'], ['nsp'], null, null],
    ['doanh thu theo vùng năm 2013', ['dt'], ['vung'], '2013', null],
    ['số đơn internet theo tháng', ['sdi'], [], null, 'month'],
    ['top 5 sản phẩm theo doanh thu', ['dt'], ['sp'], null, null],
    ['so sánh doanh thu quý 3 và quý 4 năm 2013', ['dt'], [], '2013', 'quarter'],
    ['Doanh thu Internet tháng 8/2013', ['dti'], [], '2013-08', null],
  ])('%s', (question, metrics, dimensions, period, grain) => {
    const read = readQuestion(question, model);
    expect(read.metric_ids).toEqual(metrics);
    expect(read.dimension_ids).toEqual(dimensions);
    expect(read.period ?? null).toBe(period);
    expect(read.grain ?? null).toBe(grain);
  });

  it('says it read no metric rather than guessing one', () => {
    expect(() => readQuestion('khách hàng rời bỏ', model)).toThrow(/Chưa nhận ra chỉ số/);
  });
});

describe('readExploreResult (K3: titles and the change column)', () => {
  it('names a cut read at a grain by the grain, and lists no regions as compared periods', () => {
    const run = { id: 'r', duration_ms: 100, row_count: 2, data_space_id: 's' } as never;
    const data = {
      columns: [{ name: 'ngay' }, { name: 'vung' }, { name: 'value' }],
      rows: [
        ['2013-Q1', 'Europe', 1],
        ['2013-Q1', 'Pacific', 2],
      ],
      truncated: false,
    } as never;
    const read = readExploreResult(
      run,
      data,
      { metric_ids: ['dti'], dimension_ids: ['vung'], filters: [], grain: 'quarter', period: '2013' },
      model,
    );
    expect(read.title).toBe('Doanh thu Internet theo quý · năm 2013');
    expect(read.series.map((s) => s.label)).toEqual(['Europe', 'Pacific']);
    // Regions are no periods: no "Thay đổi".
    expect(read.has_delta).toBe(false);
  });
});
