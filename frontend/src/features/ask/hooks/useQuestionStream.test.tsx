import { act, renderHook, waitFor } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { setDataSpace } from '../../../lib/v1/client';
import { createQuestionHandlers } from '../../../mocks/questions';
import { readVerification, useQuestionStream } from './useQuestionStream';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createQuestionHandlers()));
afterEach(() => {
  server.resetHandlers();
  setDataSpace(null);
});
afterAll(() => server.close());

describe('useQuestionStream', () => {
  it('AC-01-01-01 records the question, follows its stream and keeps the server answer id', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].status).toBe('done'));
    const answer = result.current.messages[1];
    expect(answer.questionId).toBeTruthy();
    expect(answer.answerId).toBeTruthy();
    expect(answer.content).toContain('1,84%');
    expect(answer.progress).toBeUndefined();
    // the figures are drawn: a bar per region, the table, the label, and the SQL behind them
    expect(answer.chartSpecs?.[0]).toMatchObject({ chart_type: 'bar', x_key: 'Vùng', y_keys: ['Tỷ lệ nợ xấu'] });
    expect(answer.chartSpecs?.[0].data).toHaveLength(3);
    expect(answer.evidence?.runs[0].columns).toEqual(['Vùng', 'Tỷ lệ nợ xấu']);
    expect(answer.trust?.label).toBe('Theo chỉ số chuẩn');
    await waitFor(() => expect(result.current.messages[1].evidence?.runs[0].sql).toContain('GROUP BY vung'));
  });

  it('AC-01-04-01 a follow-up shows the chips it inherited, and a why-question its proposal', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].reading).toBeDefined());
    const first = result.current.messages[1];
    expect(first.reading?.some((c) => c.inherited)).toBe(false);
    expect(first.newQuestion).toBe(false);
    expect(first.drill).toEqual({ axis: 'Vùng', cuts: [{ code: 'chi_nhanh', name: 'chi nhánh' }] });
    expect(first.proposal).toBeUndefined();

    await act(async () => {
      await result.current.send('Vì sao còn theo vùng thì tăng?');
    });
    await waitFor(() => expect(result.current.messages[3].reading).toBeDefined());
    const next = result.current.messages[3];
    expect(next.reading?.filter((c) => c.inherited).map((c) => c.label)).toEqual(['Chỉ số', 'Kỳ']);
    expect(next.newQuestion).toBe(false);
    expect(next.proposal?.params).toEqual({
      metric: 'NPL_RATIO',
      dimension: 'vung',
      period: '2026-09',
      base_period: '2026-08',
    });
  });

  it('asks for a Data Space when none is chosen (A1.2 data_space_required)', async () => {
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Doanh thu tháng này');
    });
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.messages[1].content).toBe('Chọn một Data Space ở thanh trên trước khi hỏi.');
  });
});

describe('readVerification', () => {
  it('R0-8 reads the checks the worker wrote with the answer', () => {
    const verification = readVerification({
      numbers_total: 2,
      verification: {
        verdict: 'FAIL',
        iteration: 1,
        checks: [
          { name: 'Số trong câu có nguồn', passed: true, detail: '2/2 số' },
          { name: 'Khớp số đã nộp', passed: false, detail: 'Số hiện tại khác số đã nộp của kỳ' },
        ],
      },
    });
    expect(verification?.verdict).toBe('FAIL');
    expect(verification?.checks.map(c => c.passed)).toEqual([true, false]);
  });

  it('draws no verdict from anything else', () => {
    expect(readVerification({ numbers_total: 2 })).toBeUndefined();
    expect(readVerification({ verification: { verdict: 'MAYBE', checks: [] } })).toBeUndefined();
    expect(readVerification(null)).toBeUndefined();
  });
});
