import { describe, expect, it } from 'vitest';
import { answerTrust, runTrust } from './trust';
import type { EvidenceRun } from './types';

const run = (over: Partial<EvidenceRun>): EvidenceRun => ({
  kind: 'sql', sql: 'SELECT 1', generated_by: 'llm', columns: [], rows: [], ...over,
});

describe('trust label', () => {
  it('uses the backend label, else what the source implies', () => {
    expect(runTrust(run({ trust: 'VERIFIED' }))).toBe('VERIFIED');
    expect(runTrust(run({ generated_by: 'semantic_layer' }))).toBe('GOVERNED');
    expect(runTrust(run({ generated_by: 'verified_query' }))).toBe('VERIFIED');
    expect(runTrust(run({ generated_by: 'llm' }))).toBe('EXPLORATORY');
    // an unknown label is never read as stronger than the weakest known one
    expect(runTrust(run({ trust: 'SOMETHING_NEW', generated_by: 'semantic_layer' }))).toBe('GOVERNED');
    expect(runTrust(run({ trust: 'SOMETHING_NEW', generated_by: 'mystery' }))).toBe('EXPLORATORY');
  });

  it('labels the answer with its weakest result and says how they split', () => {
    const t = answerTrust([run({ trust: 'GOVERNED' }), run({ trust: 'GOVERNED' }), run({ trust: 'EXPLORATORY' })]);
    expect(t.label).toBe('Khám phá');
    expect(t.breakdown).toBe('2 kết quả Có quản trị · 1 kết quả Khám phá');
  });

  it('has no breakdown when every result agrees', () => {
    expect(answerTrust([run({ trust: 'VERIFIED' })])).toMatchObject({ label: 'Đã kiểm', breakdown: undefined });
  });

  it('labels document-only and unsupported answers too', () => {
    expect(answerTrust([], [{ index: 1, source: 'Thông tư', type: 'document', snippet: '' }]).label).toBe('Giả thuyết từ tài liệu');
    expect(answerTrust([]).label).toBe('Chưa đủ bằng chứng');
  });
});
