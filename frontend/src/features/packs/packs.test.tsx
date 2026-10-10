/**
 * Gói ngành against the v1 mock: the pack list, installing a pack with its code conflicts,
 * onboarding (mapping an entity, then calibrating until it can finish), adjusting a metric,
 * upgrading with a three-way merge, and publishing a draft.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import MetricOverridePage from './MetricOverridePage';
import PackAuthorPage from './PackAuthorPage';
import PackCalibrationPage from './PackCalibrationPage';
import PackInstallPage from './PackInstallPage';
import PackOnboardPage from './PackOnboardPage';
import PacksPage from './PacksPage';
import PackUpgradePage from './PackUpgradePage';

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

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('PacksPage', () => {
  it('lists installed and available packs and opens what each leads to', async () => {
    wrap(<PacksPage />);
    const retail = await screen.findByRole('row', { name: /Ngân hàng · bán lẻ/ });
    expect(within(retail).getByText('Có bản 2026.4')).toBeInTheDocument();
    expect(within(retail).getByText('92% · 46/50 thực thể')).toBeInTheDocument();
    fireEvent.click(within(retail).getByRole('button', { name: /Thao tác với/ }));
    fireEvent.click(within(retail).getByRole('menuitem', { name: 'Nâng cấp lên 2026.4' }));
    expect(navigate).toHaveBeenCalledWith('data', 'PACK_UPGRADE', 'upg-retail-2026-4');

    fireEvent.click(screen.getByRole('button', { name: 'Bảo hiểm nhân thọ' }));
    expect(navigate).toHaveBeenCalledWith('data', 'PACK_INSTALL', 'pv-life-2026-2');
    fireEvent.click(within(retail).getByRole('button', { name: '14 chỉ số' }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC_OVERRIDE', 'ovr-npl-ratio');
  });

  it('imports a pack file and opens its preview', async () => {
    wrap(<PacksPage />);
    await screen.findByRole('row', { name: /Ngân hàng · bán lẻ/ });
    fireEvent.change(screen.getByLabelText('Tệp gói ngành'), {
      target: { files: [new File(['x'], 'life.dapack')] },
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('data', 'PACK_INSTALL', 'pv-life-2026-2'),
    );
  });
});

describe('PackInstallPage', () => {
  it('shows the checks and code conflicts, and installs into the ticked Data Spaces', async () => {
    routeId = 'pv-life-2026-2';
    wrap(<PackInstallPage />);
    expect(await screen.findByText('2 chỉ số trùng mã với chỉ số đã có')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Cách xử lý NPL_RATIO' })).toHaveTextContent(
      'Giữ bản của tổ chức, liên kết với gói',
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Tài chính' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cài vào 2 Data Space' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('data', 'PACK_ONBOARD', 'inst-credit-risk'),
    );
  });
});

describe('PackOnboardPage', () => {
  it('opens the first entity not fully mapped and approves the ticked rows once the required one has a column', async () => {
    wrap(<PackOnboardPage />);
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Khách hàng' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chọn kyc_level' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt ánh xạ đã chọn' }));
    expect(
      await screen.findByText(/Chọn cột hoặc “Không áp dụng” cho kyc_level/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: 'Cột cho kyc_level' }));
    fireEvent.click(await screen.findByRole('option', { name: 'EKYC.LEVEL' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt ánh xạ đã chọn' }));
    expect(await screen.findByText('Đã duyệt 4 ánh xạ của Khách hàng')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Xem bảng gộp' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Bảng gộp income_band' });
    expect(within(dialog).getByText('7 · trên 100 triệu')).toBeInTheDocument();
  });
});

describe('PackCalibrationPage', () => {
  it('keeps finishing closed until the gap is fixed, then finishes', async () => {
    routeId = 'inst-credit-risk';
    wrap(<PackCalibrationPage />);
    expect(await screen.findByText('5/6 khớp')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hoàn tất đưa vào ngành' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Loại thẻ phụ khỏi ánh xạ' }));
    expect(await screen.findByText('6/6 khớp')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hoàn tất đưa vào ngành' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'PACKS'));
  });

  it('accepts a gap only with a note', async () => {
    routeId = 'inst-credit-risk';
    wrap(<PackCalibrationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ghi chú lệch được chấp nhận' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Chấp nhận phần lệch?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Chấp nhận' }));
    expect(await within(dialog).findByText('Ghi lý do chấp nhận phần lệch.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Báo cáo NHNN không tính thẻ phụ theo quy định' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Chấp nhận' }));
    expect(await screen.findByText('Chấp nhận lệch')).toBeInTheDocument();
  });
});

describe('MetricOverridePage', () => {
  it('runs the 6-period trial before sending for certification', async () => {
    routeId = 'ovr-npl-ratio';
    wrap(<MetricOverridePage />);
    expect(await screen.findByText('2 điểm khác')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gửi chứng nhận' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Chạy thử 6 kỳ' }));
    expect(await screen.findByText(/khớp số đã báo cáo cả 6 kỳ/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Gửi chứng nhận' }));
    expect(await screen.findByText('Chờ chứng nhận')).toBeInTheDocument();
  });
});

describe('PackUpgradePage', () => {
  it('needs the benchmark before applying, then applies the choices', async () => {
    routeId = 'upg-retail-2026-4';
    wrap(<PackUpgradePage />);
    expect(
      await screen.findByRole('combobox', { name: 'Chọn cho NPL_RATIO · bộ lọc' }),
    ).toHaveTextContent('Gộp cả hai (đề xuất)');
    expect(screen.getByRole('button', { name: 'Áp dụng nâng cấp' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('tab', { name: /Bỏ qua/ }));
    expect(screen.getByText('tổ chức đã xoá chỉ số này')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy bộ câu hỏi chuẩn' }));
    expect(await screen.findByText(/Bộ câu hỏi chuẩn: 97,2% đúng \(cần/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Áp dụng nâng cấp' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'PACKS'));
  });
});

describe('PackAuthorPage', () => {
  it('keeps publishing closed while a check rule fails', async () => {
    wrap(<PackAuthorPage />);
    expect(await screen.findByText('82/84 đạt')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Phát hành 2026.4' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm thử' }));
    expect(await screen.findByText('Đang kiểm thử trên bộ dữ liệu mẫu')).toBeInTheDocument();
  });
});
