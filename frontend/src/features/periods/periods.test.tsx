/**
 * Chốt kỳ against the v1 mock: the close under way opens by default, a reconciliation mismatch
 * is decided inline (with a required note), which unlocks "Khoá kỳ"; locking asks first.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { getPeriod, getRetroChange } from '../../lib/v1/periods';
import { createPeriodHandlers } from '../../mocks/periods';
import CompareVersionsPage, { csvCell } from './CompareVersionsPage';
import PeriodClosePage from './PeriodClosePage';
import RetroChangePage from './RetroChangePage';

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

describe('RetroChangePage', () => {
  const retro = () =>
    render(
      <ToastProvider>
        <RetroChangePage />
      </ToastProvider>,
    );

  it('shows what changed at the source and which locked figures move, published ones flagged', async () => {
    routeId = 'retro-012';
    retro();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Dữ liệu kỳ T8 thay đổi sau khi khoá/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /LD_LOANS 214/ })).toBeInTheDocument();
    const npl = screen.getByRole('row', { name: /Nợ xấu bán lẻ/ });
    expect(within(npl).getByText('Có · NHNN T8')).toHaveClass('text-warning');
    expect(screen.getByText(/Pháp chế quyết có gửi đính chính/)).toBeInTheDocument();
  });

  it('creates the adjusted version only with a reason', async () => {
    routeId = 'retro-012';
    retro();
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo bản điều chỉnh T8 · v2' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Tạo bản điều chỉnh T8 · v2?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo bản điều chỉnh' }));
    expect(await within(dialog).findByText('Nhập lý do trước khi ghi nhận.')).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Core banking sửa lô ngày quá hạn sau khoá.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo bản điều chỉnh' }));
    expect(await screen.findByText(/Lý do quyết định: Core banking sửa lô/)).toBeInTheDocument();
    expect((await getRetroChange('retro-012')).status).toBe('new_version');
    expect(screen.queryByRole('button', { name: 'Tạo bản điều chỉnh T8 · v2' })).toBeNull();
  });
});

describe('CompareVersionsPage', () => {
  it('compares two versions figure by figure and links each gap to its retro change', async () => {
    render(
      <ToastProvider>
        <CompareVersionsPage />
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', { level: 1, name: 'So sánh T8 · v1 với T8 · v2' }),
    ).toBeInTheDocument();
    const npl = screen.getByRole('row', { name: /Nợ xấu bán lẻ/ });
    expect(within(npl).getByText('+0,04 điểm')).toHaveClass('text-warning');
    expect(within(screen.getByRole('row', { name: /Dư nợ cho vay/ })).getByText('0')).toHaveClass(
      'text-muted-foreground',
    );
    fireEvent.click(within(npl).getByRole('button', { name: /RC-012/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'RETRO', 'retro-012');

    fireEvent.click(screen.getByRole('button', { name: 'Đổi bản' }));
    expect(
      await screen.findByRole('heading', { level: 1, name: 'So sánh T8 · v2 với T8 · v1' }),
    ).toBeInTheDocument();
  });
});

describe('CSV export', () => {
  it('keeps spreadsheet formulas from running and quotes every cell', () => {
    expect(csvCell('+0,04 điểm')).toBe(`"'+0,04 điểm"`);
    expect(csvCell('=HYPERLINK("http://x")')).toBe(`"'=HYPERLINK(""http://x"")"`);
    for (const lead of ['-', '@', '\t', '\r']) expect(csvCell(`${lead}1`)).toBe(`"'${lead}1"`);
    expect(csvCell('1,62%')).toBe('"1,62%"');
  });
});
