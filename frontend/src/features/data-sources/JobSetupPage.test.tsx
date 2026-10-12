/** Job nạp · Thiết lập against the v1 mock: suggested settings, a dry run, then save. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import JobSetupPage from './JobSetupPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: undefined as string | undefined }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
});
afterAll(() => server.close());

describe('JobSetupPage', () => {
  it('dry-runs the suggested incremental job and saves it', async () => {
    render(
      <ToastProvider>
        <JobSetupPage />
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Job nạp · card_transactions' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Tăng dần' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('combobox', { name: 'Cột con trỏ' })).toHaveTextContent(
      'updated_at · TIMESTAMP',
    );
    expect(
      screen.getByText('Agent gợi ý: cột này luôn được cập nhật khi dòng đổi'),
    ).toBeInTheDocument();
    expect(screen.getByText('~180.000 dòng')).toBeInTheDocument();
    expect(screen.getByText(/Chưa chạy thử/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chạy thử 1.000 dòng' }));
    expect(await screen.findByText('3 · sẽ vào hàng bản ghi lỗi')).toBeInTheDocument();
    expect(screen.getByText('Đọc được theo con trỏ')).toBeInTheDocument();

    // A change makes the dry run stale.
    fireEvent.click(screen.getByRole('radio', { name: 'Theo lịch' }));
    expect(screen.queryByText('Đọc được theo con trỏ')).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Cột con trỏ' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu job' }));
    await waitFor(() => expect(app.navigate).toHaveBeenCalledWith('data', 'JOBS'));
  });
});
