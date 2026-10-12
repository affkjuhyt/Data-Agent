import { describe, expect, it } from 'vitest';
import { readMetricOverride, type MetricOverrideWire } from './packs';

describe('readMetricOverride', () => {
  it('adapts the API override detail to the formula comparison screen', () => {
    const override = readMetricOverride({
      id: 'override-1',
      pack_install_id: 'install-1',
      pack_metric_code: 'NPL_RATIO',
      expression: 'npl / loans where internal = false',
      scope_condition: 'segment = retail',
      reason: 'Khớp số báo cáo đã nộp',
      status: 'pending_review',
      approved_by: null,
      compliance_approved_by: null,
      calibration_run_id: null,
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
      metric_name: 'Tỷ lệ nợ xấu',
      base_expression: 'npl / loans',
      in_regulatory_report: true,
      awaiting: 'approve',
      calibration_run: null,
    } as MetricOverrideWire);

    expect(override).toMatchObject({
      metric_code: 'NPL_RATIO',
      status: 'submitted',
      base_expression: 'npl / loans',
      org_expression: 'npl / loans where internal = false',
      changes: [{ reason: 'Khớp số báo cáo đã nộp' }],
    });
  });
});
