import { describe, expect, it } from 'vitest';

import { sampleQuestion } from './sampleQuestion';
import type { ExploreModel } from './v1/explore';

const model = {
  metrics: [
    { id: 'm1', label: 'Doanh thu Internet', code: 'DT', certified: true, allowed_dimension_ids: ['d1'] },
  ],
  dimensions: [{ id: 'd1', code: 'khu_vuc', label: 'Khu vực' }],
} as unknown as ExploreModel;

describe('sampleQuestion', () => {
  it("is built from the Data Space's own metric and the cut it allows", () => {
    expect(sampleQuestion(model)).toBe('doanh thu Internet theo khu vực tháng này');
    expect(sampleQuestion(model, 'compare_quarter')).toBe(
      'so sánh doanh thu Internet theo khu vực quý này với quý trước',
    );
  });

  it('names no metric before the model is loaded', () => {
    expect(sampleQuestion(undefined)).toBe('một chỉ số theo tháng năm nay');
  });
});
