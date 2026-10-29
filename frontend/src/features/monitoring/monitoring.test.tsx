/**
 * Theo dõi against the v1 mock: the list with its filters and pausing, creating a monitor on a
 * published metric, and one alert.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AlertPage from './AlertPage';
import WatchCreatePage from './WatchCreatePage';
import WatchListPage from './WatchListPage';
import { whenText } from './whenText';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, routeId, dataSpace: { id: 'ds-rr', name: 'Rủi ro tín dụng' } }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('WatchListPage', () => {
  it('lists the monitors, filters them, and pauses one', async () => {
    wrap(<WatchListPage />);
    expect(
      await screen.findByText(/^14 theo dõi trong Rủi ro tín dụng · 3 đang cảnh báo/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(15);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Đang cảnh báo' }));
    expect(screen.getAllByRole('row')).toHaveLength(4);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Tôi' }));
    expect(screen.getAllByRole('row')).toHaveLength(2);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Đang cảnh báo' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Tôi' }));

    fireEvent.change(screen.getByRole('textbox', { name: 'Tìm theo dõi' }), {
      target: { value: 'tỷ lệ thu hồi' },
    });
    const row = screen.getByRole('row', { name: /Tỷ lệ thu hồi nợ/ });
    fireEvent.click(within(row).getByRole('button', { name: /Thao tác với/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Tạm dừng' }));
    expect(await screen.findByText('Đã tạm dừng Tỷ lệ thu hồi nợ')).toBeInTheDocument();
    expect(
      await within(screen.getByRole('row', { name: /Tỷ lệ thu hồi nợ/ })).findByText('Tạm dừng'),
    ).toBeInTheDocument();
  });

  it('opens the latest alert of a monitor', async () => {
    wrap(<WatchListPage />);
    const row = await screen.findByRole('row', { name: /Nợ xấu bán lẻ · DH Bắc Bộ/ });
    fireEvent.click(within(row).getByRole('button', { name: /Thao tác với/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mở cảnh báo gần nhất' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'ALERTS', 'alert-npl-north');
  });
});

describe('WatchCreatePage', () => {
  it('creates a monitor on a published metric with the rule in A3 words', async () => {
    let sent: Record<string, unknown> | undefined;
    server.use(
      http.post('*/v1/monitors', async ({ request }) => {
        sent = (await request.clone().json()) as Record<string, unknown>;
        return undefined;
      }),
    );
    wrap(<WatchCreatePage />);
    expect(await screen.findByRole('button', { name: 'Chỉ số' })).toHaveTextContent('NPL_RATIO');
    // The splits are the dimensions of the metric's current version.
    fireEvent.click(await screen.findByRole('button', { name: 'Tách theo' }));
    fireEvent.click(await screen.findByRole('option', { name: 'region' }));
    expect(screen.queryByRole('radio', { name: 'Dự báo' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tạo theo dõi' }));
    expect(await screen.findByText('Đã tạo theo dõi NPL_RATIO')).toBeInTheDocument();
    expect(navigate).toHaveBeenCalledWith('analysis', 'WATCH');
    expect(sent).toMatchObject({
      metric_id: 'metric-npl-ratio',
      scope_filter: { split: 'region' },
      rule_kind: 'threshold',
      rule: { direction: 'above', value: 2 },
      notify_mode: 'with_analysis',
      recipients: [
        {
          principal_type: 'user',
          principal_id: '01920000-0000-7000-8000-00000000a5c1',
          channels: ['teams', 'email'],
        },
      ],
    });
  });
});

describe('AlertPage', () => {
  it('opens the newest open alert from the rail, with its cause, and investigates it', async () => {
    wrap(<AlertPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /Nợ xấu bán lẻ DH Bắc Bộ vượt 2,0%/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Đang cảnh báo')).toBeInTheDocument();
    expect(
      screen.getByRole('row', { name: /Tiêu dùng không TSBĐ · CN Hải Phòng \+0,24/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem truy vấn' }));
    expect(screen.getByText(/FROM mart.npl_daily/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở điều tra' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('analysis', 'SESSIONS', 'ws-alert-alert-npl-north'),
    );
  });

  it('closes as a false alarm only with a note, then reopens', async () => {
    routeId = 'alert-npl-north';
    wrap(<AlertPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Đóng do báo nhầm' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Đóng #A-2291 do báo nhầm?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Đóng cảnh báo' }));
    expect(await within(dialog).findByText(/agent học từ ghi chú này/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Ghi chú (bắt buộc)'), {
      target: { value: '19 khoản tái cơ cấu đã được điều chỉnh, số thật dưới ngưỡng.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Đóng cảnh báo' }));
    expect(await screen.findByText('Đã đóng · báo nhầm')).toBeInTheDocument();
    expect(screen.getByText(/Agent ghi nhớ: khi giải thích nợ xấu/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở lại' }));
    const reopen = screen.getByRole('alertdialog', { name: 'Mở lại #A-2291?' });
    fireEvent.click(within(reopen).getByRole('button', { name: 'Mở lại' }));
    expect(await within(reopen).findByText('Ghi vì sao mở lại.')).toBeInTheDocument();
    fireEvent.change(within(reopen).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Sổ cái điều chỉnh lại, số vượt ngưỡng trở lại.' },
    });
    fireEvent.click(within(reopen).getByRole('button', { name: 'Mở lại' }));
    expect(await screen.findByText('Đang cảnh báo')).toBeInTheDocument();
  });

  it('hands the alert to someone else', async () => {
    routeId = 'alert-npl-north';
    wrap(<AlertPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Giao cho người khác' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Giao #A-2291 cho người khác' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Người nhận' }));
    const option = (await screen.findAllByRole('option'))[0];
    const name = option.textContent ?? '';
    fireEvent.click(option);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Giao' }));
    expect(await screen.findByText(`Đã giao #A-2291 cho ${name}`)).toBeInTheDocument();
  });
});

describe('whenText', () => {
  it('writes today, yesterday and older moments the way the lists do', () => {
    const now = new Date(2026, 9, 11, 12, 0);
    expect(whenText(new Date(2026, 9, 11, 7, 15).toISOString(), now)).toBe('07:15 hôm nay');
    expect(whenText(new Date(2026, 9, 10, 18, 0).toISOString(), now)).toBe('hôm qua 18:00');
    expect(whenText(new Date(2026, 9, 3, 9, 0).toISOString(), now)).toBe('03/10');
  });
});
