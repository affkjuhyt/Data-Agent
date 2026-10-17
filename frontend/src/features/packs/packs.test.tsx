/**
 * Gói ngành against the v1 mock (the api's shapes): the pack list, installing a pack version,
 * onboarding (mapping an entity, then calibrating and finishing or adjusting a metric), reading an
 * override, upgrading with a three-way merge, and a draft that cannot be published yet.
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

describe('CreatePackDialog', () => {
  it('creates the organisation’s own pack and opens its first draft', async () => {
    wrap(<PacksPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo gói riêng' }));
    const dialog = screen.getByRole('dialog', { name: 'Tạo gói domain riêng' });
    const create = within(dialog).getByRole('button', { name: 'Tạo gói' });
    expect(create).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(within(dialog).getByLabelText('Tên gói'), {
      target: { value: 'Tín dụng tiêu dùng · nội bộ' },
    });
    fireEvent.click(create);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'PACK_AUTHOR', 'pv-own-1'));
  });
});

describe('PackInstallPage', () => {
  it('shows what the version adds and its checks, and installs into the Data Space', async () => {
    routeId = 'pv-life-2026-2';
    wrap(<PackInstallPage />);
    expect(
      await screen.findByRole('heading', { name: 'Cài “Bảo hiểm nhân thọ” 2026.2' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Chữ ký của nhà phát hành')).toBeInTheDocument();
    expect(screen.getByText('112')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cài vào Data Space này' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('data', 'PACK_ONBOARD', 'inst-credit-risk'),
    );
  });
});

describe('PackOnboardPage', () => {
  it('opens the first entity not fully mapped and approves the ticked rows once the required one is settled', async () => {
    wrap(<PackOnboardPage />);
    expect(
      // Four requests in a row: the install list, the install, its version and its mappings.
      await screen.findByRole('heading', { level: 2, name: 'Khách hàng' }, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/kyc_level là thuộc tính bắt buộc/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chọn kyc_level' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt ánh xạ đã chọn' }));
    expect(
      await screen.findByText(/Chọn cột hoặc “Không áp dụng” cho kyc_level/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: 'Cột cho kyc_level' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Không áp dụng' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Chọn income_band' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt ánh xạ đã chọn' }));
    expect(await screen.findByText('Đã duyệt 4 ánh xạ của Khách hàng')).toBeInTheDocument();
  });
});

describe('PackCalibrationPage', () => {
  it('shows the gap with its cause and finishes once the run is done', async () => {
    routeId = 'inst-credit-risk';
    wrap(<PackCalibrationPage />);
    expect(await screen.findByText('5/6 khớp')).toBeInTheDocument();
    expect(screen.getByText(/không tính 2.140 thẻ phụ/)).toBeInTheDocument();
    expect(screen.queryByText('Ghi chú lệch được chấp nhận')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hoàn tất đưa vào ngành' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'PACKS'));
  });

  it('accepts a gap only through an override sent for approval', async () => {
    routeId = 'inst-credit-risk';
    wrap(<PackCalibrationPage />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Hiệu chỉnh CARD_BALANCE cho tổ chức' }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Hiệu chỉnh CARD_BALANCE' });
    const send = within(dialog).getByRole('button', { name: 'Gửi duyệt' });
    expect(send).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(within(dialog).getByLabelText('Công thức của tổ chức'), {
      target: { value: 'sum(card.balance) where card.kind = main' },
    });
    fireEvent.change(within(dialog).getByLabelText('Lý do'), {
      target: { value: 'Báo cáo NHNN không tính thẻ phụ' },
    });
    fireEvent.click(send);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'METRIC_OVERRIDE', 'ovr-2'));
  });

  it('reconciles again on request', async () => {
    routeId = 'inst-credit-risk';
    wrap(<PackCalibrationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Đối chiếu lại' }));
    expect(await screen.findByText('Đang đối chiếu')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hoàn tất đưa vào ngành' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

describe('MetricOverridePage', () => {
  it('shows the override waiting for approval with its run against the filed numbers', async () => {
    routeId = 'ovr-npl-ratio';
    wrap(<MetricOverridePage />);
    expect(await screen.findByText('Chờ duyệt')).toBeInTheDocument();
    expect(screen.getByText('2 điểm khác')).toBeInTheDocument();
    expect(screen.getByText(/Chờ người phụ trách chỉ số duyệt trong Cần bạn/)).toBeInTheDocument();
    expect(screen.getByText('Đối chiếu với số đã nộp: 2/2 khớp.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Chấp nhận' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Từ chối' })).not.toBeInTheDocument();
  });
});

describe('PackUpgradePage', () => {
  it('needs every choice and the test run before applying, then applies', async () => {
    routeId = 'upg-retail-2026-4';
    wrap(<PackUpgradePage />);
    expect(
      await screen.findByText('Nâng cấp 2026.3 → 2026.4', {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Áp dụng nâng cấp' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Lưu lựa chọn và chạy thử' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('tab', { name: /Bỏ qua/ }));
    expect(screen.getByText('Chỉ số CARD_ACTIVE')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Cần chọn/ }));

    fireEvent.click(screen.getByRole('combobox', { name: 'Chọn cho Chỉ số NPL_RATIO' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Giữ của tổ chức' }));
    fireEvent.click(
      screen.getByRole('combobox', { name: 'Chọn cho Ánh xạ card.supplementary_flag' }),
    );
    fireEvent.click(await screen.findByRole('option', { name: 'Dùng bản mới' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu lựa chọn và chạy thử' }));
    expect(await screen.findByText('Chạy thử với số đã nộp: 6/6 khớp.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Áp dụng nâng cấp' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'PACKS'));
  });
});

describe('PackAuthorPage', () => {
  it('keeps publishing closed while a check fails', async () => {
    wrap(<PackAuthorPage />);
    expect(await screen.findByText('1 chưa đạt')).toBeInTheDocument();
    expect(screen.getByText('CARD_ACTIVE')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Phát hành 2026.4' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm thử' }));
    expect(await screen.findByText('Đang kiểm thử trên bộ dữ liệu mẫu')).toBeInTheDocument();
  });
});
