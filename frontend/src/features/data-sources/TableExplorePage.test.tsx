/** Khám phá bảng against the v1 mock: masked rows, duplicates, filters and column statistics. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import TableExplorePage from './TableExplorePage';

const app = vi.hoisted(() => ({
  navigate: vi.fn(),
  routeId: 'tbl-ld-loans' as string | undefined,
}));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
});
afterAll(() => server.close());

const pick = (combobox: string, option: string) => {
  fireEvent.click(screen.getByRole('combobox', { name: combobox }));
  fireEvent.click(screen.getByRole('option', { name: option }));
};

describe('TableExplorePage', () => {
  it('shows masked rows, the duplicate remark and the stats of the picked column', async () => {
    render(
      <ToastProvider>
        <TableExplorePage />
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: /ld_loans · 1\.284\.562 dòng/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByText('••••1180')).toHaveLength(2);
    expect(screen.getByRole('row', { name: /Dòng 5, trùng/ })).toBeInTheDocument();
    expect(screen.getByText(/dòng 5 trùng hoàn toàn với dòng 4/)).toBeInTheDocument();

    // The first numeric column is picked; overdue_days has the histogram of Figma.
    fireEvent.click(screen.getByRole('button', { name: /overdue_days/ }));
    expect(await screen.findByText('số nguyên')).toBeInTheDocument();
    expect(screen.getByText('1.462')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Phân bố của overdue_days' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /customer_id/ }));
    expect(await screen.findByText(/không lập hồ sơ/)).toBeInTheDocument();
  });

  it('adds and removes a filter', async () => {
    render(
      <ToastProvider>
        <TableExplorePage />
      </ToastProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: '+ Thêm lọc' }));
    const dialog = await screen.findByRole('dialog');
    pick('Cột', 'debt_group');
    pick('Điều kiện', 'lớn hơn hoặc bằng');
    fireEvent.change(within(dialog).getByLabelText('Giá trị'), { target: { value: '3' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm lọc' }));
    expect(await screen.findByText(/^7 dòng khớp/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ lọc debt_group ≥ 3' }));
    expect(await screen.findByText(/^1\.284\.562 dòng khớp/)).toBeInTheDocument();
  });
});
