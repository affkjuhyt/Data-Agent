/**
 * The connect flow against the v1 mock: the first test fails the read-only check and blocks the
 * flow; after "Kiểm tra lại" it passes, tables are chosen and assigned, and saving opens the source.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ConnectPage from './ConnectPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: 'oracle' as string | undefined }));
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

describe('ConnectPage', () => {
  it('blocks a write-capable account, then saves once it is read-only', async () => {
    render(
      <ToastProvider>
        <ConnectPage />
      </ToastProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'Kết nối Oracle' })).toBeInTheDocument();
    const next = screen.getByRole('button', { name: 'Tiếp: kiểm tra' });
    expect(next).toHaveAttribute('aria-disabled', 'true');

    fireEvent.change(screen.getByLabelText('Tên hiển thị'), {
      target: { value: 'Hệ thống thẻ tín dụng' },
    });
    fireEvent.change(screen.getByLabelText('Máy chủ'), { target: { value: 'card-db.noibo' } });
    fireEvent.change(screen.getByLabelText('Tài khoản'), { target: { value: 'agent_ro' } });
    expect(
      screen.getByText('· Mở cổng từ máy chủ Data Agent tới card-db.noibo:1521'),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Mật khẩu' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      ),
    );
    pick('Mật khẩu', 'Lưu trong kho bí mật · openbao://db/card-ro');
    fireEvent.click(next);

    expect(await screen.findByText('Kết quả kiểm tra')).toBeInTheDocument();
    expect(
      screen.getByText('agent_ro có quyền INSERT, UPDATE trên CARD.CARD_LIMITS'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('REVOKE INSERT, UPDATE ON CARD.CARD_LIMITS FROM agent_ro;'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tiếp: chọn dữ liệu' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra lại' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Tiếp: chọn dữ liệu' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      ),
    );
    expect(screen.queryByText('Vì sao không đi tiếp được')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp: chọn dữ liệu' }));

    // The tables appear once the structure scan has listed them.
    fireEvent.click(await screen.findByRole('checkbox', { name: /card_accounts/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp: Data Space & lịch' }));

    const save = screen.getByRole('button', { name: 'Lưu và bắt đầu' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    const row = screen.getByText('dbo.card_accounts').closest('tr')!;
    expect(within(row).getByText('Số tài khoản · che')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole('combobox', { name: 'Data Space của dbo.card_accounts' }),
      ).toBeInTheDocument(),
    );
    await screen.findAllByText('Chọn người');
    pick('Data Space của dbo.card_accounts', 'Rủi ro tín dụng');
    // Any person from the user list will do; the fixture's names are not what this test checks.
    fireEvent.click(screen.getByRole('combobox', { name: 'Người phụ trách dbo.card_accounts' }));
    fireEvent.click((await screen.findAllByRole('option'))[0]);
    expect(screen.getByText('Nạp lần đầu khoảng 2 triệu dòng')).toBeInTheDocument();
    // A personal table is enabled only once its masking is confirmed (UC-04-04 step 3).
    expect(save).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('checkbox', { name: /xác nhận các cột dữ liệu cá nhân/ }));
    await waitFor(() => expect(save).not.toHaveAttribute('aria-disabled', 'true'));

    fireEvent.click(save);
    await waitFor(() => expect(app.navigate).toHaveBeenCalledWith('data', 'SOURCE', 'src-new-1'));
  });
});
