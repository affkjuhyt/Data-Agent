import { describe, expect, it } from 'vitest';
import { readInvestigation, readReportDocument } from './sessions';

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

describe('readInvestigation', () => {
  it('reads the plan an alert seeded and leaves other workspaces without one', () => {
    const params = {
      alert_id: 'a-1',
      metric: 'npl_ratio',
      investigation: {
        question: 'Vì sao Nợ xấu kỳ 2026-09 thay đổi so với kỳ trước?',
        plan: {
          goal: 'Tìm nguyên nhân',
          steps: [
            { id: 's1', tool: 'query_metric', instruction: 'Lấy số', depends_on: [] },
            { id: 's2', tool: 'explain_change', instruction: 'Phân rã', depends_on: ['s1'] },
          ],
        },
      },
    };
    expect(readInvestigation(params)).toEqual({
      alertId: 'a-1',
      question: 'Vì sao Nợ xấu kỳ 2026-09 thay đổi so với kỳ trước?',
      goal: 'Tìm nguyên nhân',
      steps: [
        { id: 's1', tool: 'query_metric', instruction: 'Lấy số', dependsOn: [] },
        { id: 's2', tool: 'explain_change', instruction: 'Phân rã', dependsOn: ['s1'] },
      ],
    });
    expect(readInvestigation({ period: '2026-09' })).toBeNull();
  });
});
