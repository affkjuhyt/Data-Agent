/**
 * P15 Dự báo against the v1 mock: the asynchronous answer asks for an assumption and then runs,
 * the result always carries its interval and model version, a scenario on a variable the model
 * does not use is refused, the studio turns the backtest into a model card, and the card is
 * approved or rejected with a reason.
 */
import type { ReactElement } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { resolveHitl } from '../../lib/v1/api';
import { getForecast, getForecastJob, getForecastModel } from '../../lib/v1/forecast';
import { createForecastHandlers } from '../../mocks/forecast';
import { chartRows } from './ForecastChart';
import ForecastJobPage from './ForecastJobPage';
import ForecastModelPage from './ForecastModelPage';
import ForecastPage from './ForecastPage';
import ForecastStudioPage from './ForecastStudioPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createForecastHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const show = (page: ReactElement) => render(<ToastProvider>{page}</ToastProvider>);

describe('ForecastJobPage', () => {
  beforeEach(() => {
    routeId = 'fj-2026-1010-03';
  });

  it('shows the understood intent, the steps and the assumption the agent needs', async () => {
    show(<ForecastJobPage />);
    expect(await screen.findByText('Agent Phân tích · đang tổng hợp dự báo')).toBeInTheDocument();
    expect(screen.getByText('Tầm: T11/2026 – T1/2027')).toBeInTheDocument();
    const steps = screen.getByRole('list', { name: 'Các bước của việc dự báo' });
    expect(within(steps).getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByRole('radio', { name: 'Giữ nguyên 4,50% (mặc định)' })).toBeChecked();

    fireEvent.click(screen.getByRole('link', { name: 'Xem dự báo cũ' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST', 'fc-deposit-2026-09-v6');
    fireEvent.click(screen.getByRole('button', { name: 'Sửa bộ biến' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_STUDIO', 'fj-2026-1010-03');
  });

  it('confirms the chosen assumption and moves the job on to backtest', async () => {
    show(<ForecastJobPage />);
    fireEvent.click(await screen.findByRole('radio', { name: 'Theo dự báo riêng của biến' }));
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận và chạy' }));
    expect(await screen.findByText('Đã chọn: Theo dự báo riêng của biến')).toBeInTheDocument();
    expect(screen.queryByText('Cần bạn xác nhận giả định')).not.toBeInTheDocument();
    expect(screen.getByText('Backtest 6 mô hình, 6 điểm cắt').closest('button')).toHaveAttribute(
      'aria-current',
      'step',
    );
  });

  it('links to the result once the job is done', async () => {
    await resolveHitl('hitl-forecast-policy-rate', { decision: 'answer', option: 'hold' });
    await getForecastJob('fj-2026-1010-03');
    show(<ForecastJobPage />);
    fireEvent.click(await screen.findByRole('link', { name: 'Xem kết quả dự báo' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST', 'fc-deposit-2026-11');
  });
});

describe('ForecastPage', () => {
  it('opens the first forecast with its model version, cut-off and intervals', async () => {
    show(<ForecastPage />);
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Dự báo số dư huy động bán lẻ · T11/2026 – T1/2027',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/MASE backtest 0,64 · dữ liệu đến 30\/09\/2026/)).toBeInTheDocument();
    expect(screen.getByText('Đã duyệt · Đỗ Hải Yến')).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'Dự báo theo kỳ' });
    expect(within(table).getByText('421,9 – 435,3')).toBeInTheDocument();
    expect(within(table).getByText('+3,6')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Mô hình v7' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_MODEL', 'fm-deposit-retail');
  });

  it('shows the saved scenario and offers only variables the model uses', async () => {
    routeId = 'fc-deposit-2026-11';
    show(<ForecastPage />);
    expect(await screen.findByText('Đã lưu với tên này, mở lại được sau.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ kịch bản' }));
    expect(await screen.findByRole('button', { name: 'Chạy kịch bản' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Biến của kịch bản' }));
    expect(screen.getByRole('option', { name: 'lãi suất điều hành' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Tỷ giá/ })).toBeNull();
  });

  it('labels a stale forecast before anything else', async () => {
    routeId = 'fc-deposit-2026-09-v6';
    show(<ForecastPage />);
    expect(await screen.findByText(/Dự báo cũ: dự báo cũ, dữ liệu đến 31\/08/)).toBeInTheDocument();
    expect(screen.getByText('Dự báo cũ · suy giảm')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Thêm vào phiên' })).not.toBeInTheDocument();
  });
});

describe('chartRows', () => {
  it('never draws a forecast point without its interval', async () => {
    const forecast = await getForecast('fc-deposit-2026-11');
    const broken = {
      ...forecast,
      points: [{ ...forecast.points[0], lo95: Number.NaN }, ...forecast.points.slice(1)],
    };
    const rows = chartRows(broken).filter((r) => r.mean !== undefined);
    expect(rows.map((r) => r.label)).toEqual(['T9/2026', 'T12/2026', 'T1/2027']);
    expect(rows.every((r) => r.band80 && r.band95)).toBe(true);
  });
});

describe('ForecastStudioPage', () => {
  it('shows the backtest against seasonal naive and creates the model card', async () => {
    routeId = 'fj-2026-1010-03';
    show(<ForecastStudioPage />);
    const backtest = await screen.findByRole('table', { name: 'Backtest' });
    expect(within(backtest).getByText('Ensemble 3')).toBeInTheDocument();
    expect(within(backtest).getByText('Chọn')).toBeInTheDocument();
    expect(screen.getByText('Loại · rò rỉ')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Dữ liệu' }));
    expect(screen.queryByRole('table', { name: 'Backtest' })).not.toBeInTheDocument();
    expect(screen.getByText('T4/2023 · gộp chi nhánh')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tạo thẻ mô hình v7' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_MODEL', 'fm-deposit-retail'),
    );
  });
});

describe('ForecastModelPage', () => {
  beforeEach(() => {
    routeId = 'fm-deposit-retail';
  });

  it('rejects only with a reason', async () => {
    show(<ForecastModelPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Từ chối kèm lý do' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Từ chối v7?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(await within(dialog).findByText('Nhập lý do trước khi từ chối.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Thiếu dữ liệu khuyến mại của đối thủ.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(
      await screen.findByText('Lý do từ chối: Thiếu dữ liệu khuyến mại của đối thủ.'),
    ).toBeInTheDocument();
  });

  it('approves and publishes v7 after confirmation', async () => {
    show(<ForecastModelPage />);
    expect(await screen.findByText('Theo dõi v6 · đang phát hành')).toBeInTheDocument();
    expect(screen.getAllByText('Không')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt và phát hành v7' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Duyệt và phát hành v7?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Duyệt và phát hành' }));
    expect(await screen.findByText('Thẻ mô hình · v7 · đã phát hành')).toBeInTheDocument();
    expect((await getForecastModel('fm-deposit-retail')).candidate?.status).toBe('published');
  });
});
