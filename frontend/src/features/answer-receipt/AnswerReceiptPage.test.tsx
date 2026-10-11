/**
 * P01 Biên nhận câu trả lời against the v1 mock: every figure leads back to its run, the model is
 * named (and flagged when external), a figure past its window or without a value says so, a re-run
 * reports what reproduced, and an answer without a receipt or behind a Data Space reads as such.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AnswerReceiptPage from './AnswerReceiptPage';

let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate: vi.fn(), routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = 'answer-e5ron92-0207';
  server.resetHandlers(...createHandlers());
});
afterAll(() => server.close());

const show = () =>
  render(
    <ToastProvider>
      <AnswerReceiptPage />
    </ToastProvider>,
  );

describe('AnswerReceiptPage', () => {
  it('shows the locked receipt and each figure back to its run', async () => {
    show();
    expect(await screen.findByText('BN-20260702-000184')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: 'Biên nhận câu trả lời' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Đã khoá')).toBeInTheDocument();
    // The data product in the meta line is a link to it (P07 Data product).
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === 'P' &&
          el.textContent === '02/07/2026 16:05 · Giá xăng dầu trong nước v3 · Trần Văn Khánh',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Giá xăng dầu trong nước v3' })).toBeInTheDocument();
    expect(screen.getByText(/SHA-256 biên nhận\s+4b7e91c2…a90c/)).toBeInTheDocument();
    expect(screen.getByText('Số trong câu trả lời · 2')).toBeInTheDocument();

    const f1 = screen.getByRole('article', { name: 'Số F1' });
    expect(within(f1).getByText('19.730 đ/lít')).toBeInTheDocument();
    expect(within(f1).getByText('Còn hạn')).toBeInTheDocument();
    expect(
      within(f1).getByText('gia_tran_ban_le v2 · ký bởi Nguyễn Thị Hạnh 15/06/2026'),
    ).toBeInTheDocument();
    expect(
      within(f1).getByText('gia_xang_dau_ky_dieu_hanh v41 · as_of 02/07/2026'),
    ).toBeInTheDocument();
    expect(within(f1).getByText('SHA 9f3c…e1 · semcomp 1.8.2')).toBeInTheDocument();
    expect(within(f1).getByText('nạp 02/07 15:12 · hạn 16:30')).toBeInTheDocument();

    const f2 = screen.getByRole('article', { name: 'Số F2' });
    expect(within(f2).getByText('compute_run · hiệu hai kỳ 25/06 và 02/07')).toBeInTheDocument();
    expect(within(f2).getByText('F1 và giá trần kỳ 25/06/2026 = 20.785 đ/lít')).toBeInTheDocument();

    expect(screen.getByText('Qwen3.6-27B · vLLM tại chỗ')).toBeInTheDocument();
    expect(screen.queryByText(/Có dùng mô hình bên ngoài/)).toBeNull();
    expect(
      screen.getByText('2/2 số tái lập y hệt (19.730; 1.055) · SHA-256 SQL và kết quả khớp'),
    ).toBeInTheDocument();
  });

  it('re-runs figures and words at the old point in time', async () => {
    show();
    fireEvent.click(await screen.findByRole('radio', { name: 'Chỉ số và câu chữ' }));
    fireEvent.click(screen.getByRole('button', { name: 'Chạy lại' }));
    expect(
      await screen.findByText(/^Kỳ điều hành 02\/07\/2026, giá trần xăng E5RON92/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('2/2 số tái lập y hệt (19.730; 1.055) · SHA-256 SQL và kết quả khớp'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Câu chữ đã sinh lại/)).toBeInTheDocument();
  });

  it('says when a figure is past its window, missing, or the model ran outside', async () => {
    routeId = 'answer-e5ron95-0207';
    show();
    const f1 = await screen.findByRole('article', { name: 'Số F1' });
    expect(
      within(f1).getByText('F1 · dữ liệu đến 25/06/2026, đã quá hạn cập nhật (hạn 02/07 16:30)'),
    ).toBeInTheDocument();
    expect(within(f1).queryByText('Còn hạn')).toBeNull();
    const f2 = screen.getByRole('article', { name: 'Số F2' });
    expect(
      within(f2).getByText('F2 · CHƯA CÓ (kỳ chưa đến hạn hoặc dataset chưa có dữ liệu)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Có dùng mô hình bên ngoài: Nhà cung cấp A (API), chỉ với dữ liệu công khai',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/tái lập/)).toBeNull();
  });

  it('reads an answer the server never stored as having no receipt', async () => {
    routeId = 'lx3k2a-1f';
    show();
    expect(await screen.findByText('Câu trả lời này chưa có biên nhận')).toBeInTheDocument();
  });

  it('shows what was refused instead of the receipt', async () => {
    routeId = 'answer-provision-q3';
    show();
    expect(await screen.findByText('Bạn chưa có quyền xem biên nhận này')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText('Số trong câu trả lời', { exact: false })).toBeNull(),
    );
  });
});
