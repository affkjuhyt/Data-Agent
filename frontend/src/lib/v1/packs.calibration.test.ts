import { describe, expect, it } from 'vitest';
import { readCalibrationRun, type CalibrationRunWire } from './packs';

describe('readCalibrationRun', () => {
  it('adapts calibration items and failures to the onboarding screen', () => {
    const calibration = readCalibrationRun(
      {
        id: 'calibration-1',
        pack_install_id: 'install-1',
        trigger: 'manual',
        periods: ['2026-09'],
        tolerance_pp: 0.1,
        passed: 1,
        failed: 1,
        status: 'failed',
        finished_at: '2026-10-13T00:02:00Z',
        created_at: '2026-10-13T00:01:00Z',
        items: [
          {
            id: 'item-1',
            item_kind: 'metric',
            item_code: 'NPL_RATIO',
            period: '2026-09',
            computed_value: 2.1,
            filed_value: 2.1,
            delta: 0,
            passed: true,
            suggested_cause: null,
          },
          {
            id: 'item-2',
            item_kind: 'metric',
            item_code: 'LOAN_GROWTH',
            period: '2026-09',
            computed_value: 3.2,
            filed_value: 3.5,
            delta: -0.3,
            passed: false,
            suggested_cause: 'Bảng ánh xạ thiếu khoản vay ngoại bảng',
          },
        ],
      } as CalibrationRunWire,
      { id: 'install-1', title: 'Ngân hàng Việt Nam · 2.0' },
    );

    expect(calibration).toMatchObject({
      install_title: 'Ngân hàng Việt Nam · 2.0',
      matched: 1,
      total: 2,
      rows: [
        { metric: 'NPL_RATIO', ok: true },
        { metric: 'LOAN_GROWTH', ok: false },
      ],
      run_status: 'failed',
      periods: ['2026-09'],
      findings: [
        {
          row_id: 'item-2',
          metric_code: 'LOAN_GROWTH',
          text: 'Bảng ánh xạ thiếu khoản vay ngoại bảng',
        },
      ],
    });
  });
});
