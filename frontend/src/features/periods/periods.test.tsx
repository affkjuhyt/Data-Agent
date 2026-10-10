/**
 * Chốt kỳ against the v1 mock: the close under way opens by default, a reconciliation mismatch
 * is decided inline (with a required note), which unlocks "Khoá kỳ"; locking asks first.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { getPeriod } from '../../lib/v1/periods';
import { createPeriodHandlers } from '../../mocks/periods';
import PeriodClosePage from './PeriodClosePage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createPeriodHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <PeriodClosePage />
    </ToastProvider>,
  );

describe('PeriodClosePage', () => {
  it('opens on the period being closed, with its steps and the figures it will freeze', async () => {
    page();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Chốt kỳ tháng 9\/2026/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Đang chốt · bước 3/4')).toBeInTheDocument();
    expect(screen.getByText('182.430 tỷ')).toBeInTheDocument();
    const periods = await screen.findByRole('navigation', { name: 'Các kỳ' });
    expect(within(periods).getByText('Đã khoá · 2 điều chỉnh')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Khoá kỳ' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    fireEvent.click(within(periods).getByRole('button', { name: /T8\/2026/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'PERIOD', 'period-2026-08');
  });

  it('accepts the mismatch with a note, then locks the period after confirmation', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Chấp nhận kèm ghi chú' }));
    const save = screen.getByRole('button', { name: 'Ghi nhận' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByLabelText('Ghi chú kèm theo'), {
      target: { value: 'Theo ngày Core banking, sổ cái điều chỉnh T10.' },
    });
    fireEvent.click(save);

    // Once the step is done the enabled button replaces the disabled one and its tooltip.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Khoá kỳ' })).not.toHaveAttribute('aria-disabled'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Khoá kỳ' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Khoá kỳ T9/2026?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Khoá kỳ' }));
    await waitFor(async () => expect((await getPeriod('period-2026-09')).status).toBe('locked'));
  });

  it('waiting for the ledger keeps the period unlockable', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Chờ sổ cái điều chỉnh' }));
    expect(await screen.findByText(/Chờ sổ cái điều chỉnh TK 2141/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Khoá kỳ' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});
