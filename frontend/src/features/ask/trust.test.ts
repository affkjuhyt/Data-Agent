import { describe, expect, it } from 'vitest';
import { answerTrust, runTrust, trustOfCode } from './trust';
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

  it('AE-06: draws the badge from the code the server set, never from a label', () => {
    expect(trustOfCode('GOVERNED')).toMatchObject({ level: 'GOVERNED', label: 'Có quản trị', breakdown: undefined });
    expect(trustOfCode('DERIVED')).toMatchObject({ level: 'DERIVED', tone: 'primary' });
    expect(trustOfCode('DOCUMENT')).toMatchObject({ level: 'DOCUMENTS', label: 'Giả thuyết từ tài liệu' });
    expect(trustOfCode('INSUFFICIENT').label).toBe('Chưa đủ bằng chứng');
    // The weakest code is the badge; the figures' codes say how it breaks down.
    const mixed = trustOfCode('EXPLORATORY', { figures: { F1: 'GOVERNED', F2: 'GOVERNED', F3: 'EXPLORATORY', F4: null } });
    expect(mixed).toMatchObject({ level: 'EXPLORATORY', label: 'Khám phá' });
    expect(mixed.breakdown).toBe('2 số Có quản trị · 1 số Khám phá');
  });
});

