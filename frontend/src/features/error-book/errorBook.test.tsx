/**
 * Error Book against the v1 mock, which answers in the api's shapes: the entries filtered by
 * status, one entry with its numbers and fix, and an open entry fixed only once it has a kind and
 * a root cause (BR-179).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ErrorBookPage from './ErrorBookPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = () =>
  render(
    <ToastProvider>
      <ErrorBookPage />
    </ToastProvider>,
  );

describe('ErrorBookPage', () => {
  it('shows an entry with the wrong and right number, the fix and its regression', async () => {
    wrap();
    const entry = await screen.findByRole('region', { name: 'Mục EB-0042' });
    expect(within(entry).getByText('1.155 đ/lít')).toBeInTheDocument();
    expect(within(entry).getByText('1.055 đ/lít')).toBeInTheDocument();
    expect(within(entry).getByText('Đề xuất')).toBeInTheDocument();
    expect(within(entry).getByText(/^Lượt CI .* đạt$/)).toBeInTheDocument();
    expect(within(entry).getByRole('list', { name: 'Tiến trình' })).toHaveTextContent(
      /Mở.*Đã sửa.*Đã có hồi quy/,
    );
  });

  it('shows a forecast miss with its forecast, interval and actual, and no handler name', async () => {
    routeId = 'eb-0043';
    wrap();
    const entry = await screen.findByRole('region', { name: 'Mục EB-0043' });
    expect(within(entry).getByText(/^dự báo · khoảng 95%/)).toBeInTheDocument();
    expect(within(entry).getByText('Đã giao người xử lý')).toBeInTheDocument();
    expect(within(entry).getByText('Tự tạo khi đánh dấu đã sửa')).toBeInTheDocument();
  });

  it('filters by status and fixes an open entry once its cause is written', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('radio', { name: 'Mở 2' }));
    expect(
      within(screen.getByRole('list', { name: 'Mục Error Book' })).getAllByRole('button'),
    ).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /EB-0045/ }));
    const entry = await screen.findByRole('region', { name: 'Mục EB-0045' });
    fireEvent.change(within(entry).getByLabelText('Nguyên nhân gốc'), {
      target: { value: 'Verifier không so as_of với cam kết độ tươi khi viết bản tin' },
    });
    fireEvent.click(within(entry).getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Đã lưu loại lỗi và nguyên nhân')).toBeInTheDocument();
    fireEvent.click(
      await within(await screen.findByRole('region', { name: 'Mục EB-0045' })).findByRole(
        'button',
        { name: 'Đánh dấu đã sửa' },
      ),
    );
    const dialog = screen.getByRole('dialog', { name: 'Đánh dấu EB-0045 đã sửa' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Đã sửa gì' }));
    fireEvent.click(screen.getByRole('option', { name: 'Cấu hình model' }));
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'commit 3c3a1ae3' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Đánh dấu đã sửa' }));
    expect(
      await screen.findByText(/EB-0045 đã sửa; ca hồi quy chờ lượt CI kế tiếp/),
    ).toBeInTheDocument();
  });
});
