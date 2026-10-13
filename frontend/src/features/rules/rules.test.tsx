/**
 * Quy tắc từ quyết định against the v1 mock: the draft read from GET /v1/policies/{id}, its
 * backtest counts, the owner switching it on, and dropping it after confirmation with a reason.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { getDecisionRule } from '../../lib/v1/rules';
import { createRuleHandlers } from '../../mocks/rules';
import DecisionRulePage from './DecisionRulePage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, routeId: 'rule-core-under-2b' }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createRuleHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <DecisionRulePage />
    </ToastProvider>,
  );

describe('DecisionRulePage', () => {
  it('shows the draft, its rule from condition and action, and its backtest counts', async () => {
    page();
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: /Luôn chọn Core banking khi lệch dư nợ dưới 2 tỷ/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/THÌ dùng Core banking, ghi chú lệch vào nhật ký/)).toBeInTheDocument();
    expect(screen.getByText('Chạy thử trên 30 ngày qua')).toBeInTheDocument();
    const counts = screen.getByText('Vẫn hỏi người').closest('div') as HTMLElement;
    expect(within(counts).getByText('8')).toBeInTheDocument();
    expect(screen.getByText('Khác quyết định người đã làm').nextSibling).toHaveTextContent('1');
  });

  it('the owner switches the draft on, after which it no longer offers draft actions', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Bật quy tắc' }));
    expect(await screen.findByText('Đã bật quy tắc')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Bật quy tắc' })).toBeNull());
    expect(screen.getAllByText('Đang áp dụng').length).toBeGreaterThan(0);
  });

  it('drops the draft only after confirmation and with a reason', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Bỏ' }));
    expect(screen.getByRole('alertdialog', { name: 'Bỏ bản nháp quy tắc?' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ quy tắc' }));
    expect(screen.getByText('Ghi lý do bỏ quy tắc.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Lý do bỏ'), {
      target: { value: 'Ngưỡng 2 tỷ quá cao' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ quy tắc' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    expect((await getDecisionRule('rule-core-under-2b')).status).toBe('disabled');
  });
});
