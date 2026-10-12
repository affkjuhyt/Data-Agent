import { act, renderHook, waitFor } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { setDataSpace } from '../../../lib/v1/client';
import { createQuestionHandlers } from '../../../mocks/questions';
import { useQuestionStream } from './useQuestionStream';

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
