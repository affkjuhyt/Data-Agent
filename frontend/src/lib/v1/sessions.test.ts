import { describe, expect, it } from 'vitest';
import { readReportDocument } from './sessions';

describe('session report document adapter', () => {
  it('keeps the report markdown contract lossless for the review preview', () => {
    expect(
      readReportDocument(
        '# NPL T9\n\nTỷ lệ nợ xấu tăng trong kỳ.\n\n## Figures\n\n- 1,84% | grounded\n- 19 khoản | ungrounded\n\n## Recommendation\n\nXác nhận lại ô phân rã theo vùng trước khi phát hành.',
      ),
    ).toEqual({
      heading: 'NPL T9',
      summary: 'Tỷ lệ nợ xấu tăng trong kỳ.',
      figures: [
        { value: '1,84%', grounded: true },
        { value: '19 khoản', grounded: false },
      ],
      recommendation: 'Xác nhận lại ô phân rã theo vùng trước khi phát hành.',
    });
  });

  it('does not infer figures from ordinary prose', () => {
    expect(readReportDocument('# Báo cáo\n\nNPL tăng từ 1,62% lên 1,84%.')).toEqual({
      heading: 'Báo cáo',
      summary: 'NPL tăng từ 1,62% lên 1,84%.',
      figures: [],
      recommendation: '',
    });
  });
});
