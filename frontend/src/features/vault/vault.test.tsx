/**
 * Kho riêng against the v1 mock, whose fixture is sealed with real keys: the page opens only with
 * the vault's paper code, then reads the theses, the evidence and the scores it decrypts on the
 * device, and publishes a hypothesis whose sentence carries no threshold (BR-162).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { DEV_RECOVERY_CODE } from '../../mocks/exec';
import { createHandlers } from '../../mocks/handlers';
import { newRecoveryCode } from '../../lib/vault/crypto';
import VaultPage from './VaultPage';

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({
    navigate: vi.fn(),
    routeId: '',
    user: { id: 'u-exec-an', name: 'Nguyễn Văn An' },
  }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterAll(() => server.close());

const SLOW = { timeout: 15_000 };

async function unlock(code = DEV_RECOVERY_CODE) {
  render(
    <ToastProvider>
      <VaultPage />
    </ToastProvider>,
  );
  fireEvent.change(await screen.findByLabelText('Hoặc mã khôi phục giấy (24 từ)', {}, SLOW), {
    target: { value: code },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Mở bằng mã giấy' }));
}

describe('VaultPage', () => {
  it('opens with the paper code, decrypts the theses and memory, and locks again', async () => {
    await unlock();
    expect(await screen.findByText('Đang mở trên thiết bị này', {}, SLOW)).toBeInTheDocument();
    expect(
      await screen.findByText('Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít', {}, SLOW),
    ).toBeInTheDocument();
    expect(screen.getByText('Bằng chứng: 1 ủng hộ · 2 phản bác')).toBeInTheDocument();
    expect(
      await screen.findByText(
        'Giữ chi phí vốn dưới 4,8%; không đua lãi suất 12 tháng với Ngân hàng B.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Khoá ngay' }));
    expect(screen.getByText('Đang khoá')).toBeInTheDocument();
    expect(screen.queryByText('Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít')).toBeNull();
  }, 30_000);

  it('refuses another vault’s code', async () => {
    await unlock(newRecoveryCode());
    expect(
      await screen.findByText('Mã khôi phục không mở được kho này.', {}, SLOW),
    ).toBeInTheDocument();
  }, 30_000);

  it('shows both sides of the evidence and the calibration computed on the device', async () => {
    await unlock();
    await screen.findByText('Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít', {}, SLOW);
    fireEvent.click(screen.getByRole('tab', { name: 'Bằng chứng' }));
    expect(await screen.findByText('Ủng hộ · 1')).toBeInTheDocument();
    expect(screen.getByText('Phản bác · 2')).toBeInTheDocument();
    expect(screen.getByText(/\+1 phản bác: Dự báo kỳ 22\/10/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Chấm điểm' }));
    expect(await screen.findByText('7 · 58%')).toBeInTheDocument();
    expect(screen.getByText('1 rút · 1 không chấm được')).toBeInTheDocument();
    const log = screen.getByRole('region', { name: 'Sổ truy cập khoá' });
    expect(await within(log).findByText('ExecThesisWorkflow · score_thesis')).toBeInTheDocument();
  }, 30_000);

  it('publishes a hypothesis only without the threshold in its sentence', async () => {
    await unlock();
    await screen.findByText('Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít', {}, SLOW);
    fireEvent.click(screen.getByRole('tab', { name: 'Công bố & khoá' }));
    const sentence = await screen.findByDisplayValue(/sẽ không thấp hơn ngưỡng dưới đây/);
    fireEvent.change(sentence, { target: { value: 'Giá trần sẽ trên 20.000 đ/lít.' } });
    expect(
      screen.getByText('Bỏ ngưỡng khỏi câu; ngưỡng đã nằm trong điều kiện.'),
    ).toBeInTheDocument();
    fireEvent.change(sentence, {
      target: { value: 'Giá trần kỳ tới sẽ không thấp hơn ngưỡng dưới đây.' },
    });
    // The targets are the viewer's Data Spaces; their steward reviews.
    await screen.findByText(/Người duyệt: người phụ trách Data Space Rủi ro tín dụng/);
    expect(screen.getByRole('textbox', { name: 'Chức danh' })).toHaveValue(
      'Thành viên Ban điều hành',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Gửi duyệt công bố' }));
    expect(
      await screen.findByText('Đã gửi người phụ trách Rủi ro tín dụng duyệt'),
    ).toBeInTheDocument();
    expect(screen.getByText('Khoá bọc · 3')).toBeInTheDocument();
  }, 30_000);

  it('asks to leave with one of the reasons the api keeps', async () => {
    await unlock();
    await screen.findByText('Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít', {}, SLOW);
    fireEvent.click(screen.getByRole('tab', { name: 'Công bố & khoá' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Rời vị trí…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Niêm phong' }));
    expect(screen.queryByText('Đã niêm phong kho')).toBeNull();
    fireEvent.click(screen.getByRole('combobox', { name: 'Lý do' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Hết hợp đồng' }));
    fireEvent.click(screen.getByRole('button', { name: 'Niêm phong' }));
    expect(await screen.findByText('Đã niêm phong kho')).toBeInTheDocument();
  }, 30_000);

  it('creates a vault only once both paper codes are typed back', async () => {
    server.use(
      http.get('*/v1/exec/vault', () =>
        HttpResponse.json(
          { code: 'not_found', status: 404, detail: 'Chưa có kho.' },
          { status: 404 },
        ),
      ),
    );
    render(
      <ToastProvider>
        <VaultPage />
      </ToastProvider>,
    );
    const create = await screen.findByRole('button', { name: 'Tạo kho' });
    expect(create).toHaveAttribute('aria-disabled', 'true');
    const codes = screen.getAllByRole('list').map((list) =>
      within(list)
        .getAllByRole('listitem')
        .map((li) => li.textContent!.replace(/^\d+\.\s*/, ''))
        .join(' '),
    );
    fireEvent.change(screen.getByLabelText('Gõ lại mã 1 để thử'), { target: { value: codes[0] } });
    fireEvent.change(screen.getByLabelText('Gõ lại mã 2 để thử'), { target: { value: codes[1] } });
    expect(create).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(
      screen.getByText('Mất mọi khoá thì mất nội dung, quản trị không khôi phục được.'),
    );
    expect(create).not.toHaveAttribute('aria-disabled');
  });
});
