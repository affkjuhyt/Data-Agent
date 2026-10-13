import { describe, expect, it } from 'vitest';
import { readMetricOverride, type CalibrationRunWire, type MetricOverrideWire } from './packs';

const detail = {
  id: 'override-1',
  pack_install_id: 'install-1',
  pack_metric_code: 'NPL_RATIO',
  expression: 'npl / loans where internal = false',
  scope_condition: 'segment = retail',
  reason: 'Khớp số báo cáo đã nộp',
  status: 'pending_review',
  approved_by: null,
  compliance_approved_by: null,
  calibration_run_id: 'run-1',
  created_at: '2026-10-13T00:00:00Z',
  updated_at: '2026-10-13T00:00:00Z',
  metric_name: 'Tỷ lệ nợ xấu',
  base_expression: 'npl / loans',
  in_regulatory_report: true,
  awaiting: 'approve',
  calibration_run: {
    id: 'run-1',
    status: 'passed',
    trigger: 'override',
    passed: 1,
    failed: 0,
    created_at: '2026-10-13T00:00:00Z',
    finished_at: '2026-10-13T00:01:00Z',
  },
} as MetricOverrideWire;

const run = {
  id: 'run-1',
  items: [
    {
      id: 'i1',
      item_kind: 'metric',
      item_code: 'NPL_RATIO',
      period: '2026-09',
      computed_value: 1.84,
      filed_value: 1.84,
      delta: 0,
      passed: true,
      suggested_cause: null,
    },
    {
      id: 'i2',
      item_kind: 'metric',
      item_code: 'LOAN_BALANCE',
      period: '2026-09',
      computed_value: 1,
      filed_value: 1,
      delta: 0,
      passed: true,
      suggested_cause: null,
    },
  ],
} as CalibrationRunWire;

describe('readMetricOverride', () => {
  it('adapts the API override detail to the formula comparison screen', () => {
    expect(readMetricOverride(detail, run)).toMatchObject({
      metric_code: 'NPL_RATIO',
      status: 'submitted',
      awaiting_text: 'Chờ người phụ trách chỉ số duyệt trong Cần bạn.',
      base_expression: 'npl / loans',
      org_expression: 'npl / loans where internal = false',
      changes: [
        { change: 'Công thức của tổ chức', reason: 'Khớp số báo cáo đã nộp' },
        { change: 'Phạm vi: segment = retail', reason: 'Khớp số báo cáo đã nộp' },
      ],
      trial: [{ period: '2026-09', reported: '1.84', org: '1.84' }],
      trial_note: 'Đối chiếu với số đã nộp: 1/1 khớp.',
    });
  });

  it('reads an approved override as in force, and no run as no trial', () => {
    const active = readMetricOverride({ ...detail, status: 'active', awaiting: null });
    expect(active).toMatchObject({ status: 'certified', awaiting_text: null, trial: null });
  });
});
