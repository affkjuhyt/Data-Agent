import { describe, expect, it } from 'vitest';

import { documentStarterQuestions, sampleQuestion, starterQuestions } from './sampleQuestion';
import type { ExploreModel } from './v1/explore';

const model = {
  metrics: [
    { id: 'm1', label: 'Doanh thu Internet', code: 'DT', certified: true, allowed_dimension_ids: ['d1'] },
  ],
  dimensions: [{ id: 'd1', code: 'khu_vuc', label: 'Khu vực' }],
} as unknown as ExploreModel;

describe('sampleQuestion', () => {
  it("is built from the Data Space's own metric and the cut it allows", () => {
    // No period relative to today: the data may stop years before it.
    expect(sampleQuestion(model)).toBe('doanh thu Internet theo khu vực');
    expect(sampleQuestion(model, 'compare_quarter')).toBe('so sánh doanh thu Internet theo quý');
  });

  it('names no metric before the model is loaded', () => {
    expect(sampleQuestion(undefined)).toBe('một chỉ số theo tháng');
  });
});

describe('starterQuestions', () => {
  it('starts from the certified metrics only, in shapes the rule pass reads', () => {
    const two = {
      ...model,
      metrics: [
        { id: 'm0', label: 'Sự cố', code: 'SU_CO', certified: true, allowed_dimension_ids: [] },
        ...model.metrics,
        { id: 'm2', label: 'Số đơn', code: 'SO_DON', certified: true, allowed_dimension_ids: ['d1'] },
        { id: 'm3', label: 'Nháp', code: 'DRAFT', certified: false, allowed_dimension_ids: [] },
      ],
    } as unknown as ExploreModel;
    // The metrics cut by the most dimensions first, without "năm nay".
    expect(starterQuestions(two)).toEqual([
      'Doanh thu Internet theo khu vực',
      'Số đơn theo tháng',
      'Sự cố theo quý',
    ]);
    expect(starterQuestions(undefined)).toEqual([]);
  });
});

describe('documentStarterQuestions', () => {
  it('names the documents it asks about, each once', () => {
    expect(documentStarterQuestions(['Dealer Return and Warranty Policy', 'Dealer Return and Warranty Policy', ''])).toEqual([
      'Tóm tắt các điểm chính của tài liệu “Dealer Return and Warranty Policy”',
    ]);
    expect(documentStarterQuestions([])).toEqual([]);
  });
});
