/**
 * Quy tắc từ quyết định against the v1 mock: the draft, its backtest with the cases where it
 * disagrees with the person, sending it to its owner, and dropping it after confirmation.
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
  it('shows the draft, its backtest and where it disagrees with the person', async () => {
    page();
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: /Luôn chọn Core banking khi lệch dư nợ dưới 2 tỷ/,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/NGƯỢC LẠI hỏi Trần Quốc An qua Cần bạn/)).toBeInTheDocument();
    expect(screen.getByText('Chạy thử trên 30 ngày qua')).toBeInTheDocument();
    expect(screen.getByText('8 · vượt 2 tỷ hoặc số đã nộp')).toBeInTheDocument();
    const row = screen.getByRole('row', { name: /CN Đà Nẵng/ });
    expect(within(row).getByText('Kho dữ liệu')).toBeInTheDocument();
    expect(within(row).getByText('Core banking')).toBeInTheDocument();
  });

  it('sends the draft to its owner, after which it no longer offers draft actions', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Gửi chủ sở hữu duyệt' }));
    expect(await screen.findByText('Đã gửi Trần Quốc An duyệt')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Gửi chủ sở hữu duyệt' })).toBeNull(),
    );
    expect(screen.getAllByText('Chờ chủ sở hữu duyệt').length).toBeGreaterThan(0);
  });

  it('drops the draft only after confirmation', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Bỏ' }));
    expect(screen.getByRole('alertdialog', { name: 'Bỏ bản nháp quy tắc?' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ quy tắc' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    expect((await getDecisionRule('rule-core-under-2b')).status).toBe('disabled');
  });
});
