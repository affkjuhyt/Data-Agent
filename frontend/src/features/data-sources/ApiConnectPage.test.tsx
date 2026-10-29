/** Kết nối API against the v1 mock: a new REST source through four steps, and an existing one. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ApiConnectPage from './ApiConnectPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: undefined as string | undefined }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
  app.routeId = undefined;
});
afterAll(() => server.close());

const renderPage = () =>
  render(
    <ToastProvider>
      <ApiConnectPage />
    </ToastProvider>,
  );

describe('ApiConnectPage', () => {
  it('connects a new REST source: endpoint, secret, try, mapping, save', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Kết nối REST API' })).toBeInTheDocument();
    const tryCall = screen.getByRole('button', { name: 'Gọi thử' });
    expect(tryCall).toHaveAttribute('aria-disabled', 'true');

    fireEvent.change(screen.getByLabelText('Tên nguồn'), { target: { value: 'Thẻ qua API' } });
    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://card-api.noibo/v2/statements' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Xác thực/ }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Thông tin xác thực' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Thông tin xác thực' }));
    fireEvent.click(screen.getAllByRole('option')[0]);

    fireEvent.click(tryCall);
    expect(await screen.findByText('200 · 412 ms')).toBeInTheDocument();
    expect(screen.getByLabelText('Cột đích của data[].loan_ref')).toHaveValue('loan_id');
    expect(screen.getByText('Liên kết Core banking · khớp 97,8%')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu & lên lịch' }));
    await waitFor(() =>
      expect(app.navigate).toHaveBeenCalledWith(
        'data',
        'SOURCE',
        expect.stringMatching(/^src-api-/),
      ),
    );
  });

  it('opens an existing source on its mapping, with health and a backfill', async () => {
    app.routeId = 'src-card';
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'Nguồn thẻ tín dụng · REST API' }),
    ).toBeInTheDocument();
    expect(screen.getByText('page tăng tới khi rỗng · tối đa 200')).toBeInTheDocument();
    expect(screen.getByText('Mỗi 2 giờ · tăng dần theo updated_since')).toBeInTheDocument();
    expect(screen.getByText('380 ms')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy bù 24–29/09' }));
    expect(await screen.findByText('Đã xếp lịch chạy bù 24–29/09.')).toBeInTheDocument();
  });
});
