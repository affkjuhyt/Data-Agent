/**
 * P15 giá xăng dầu against the v1 mock: the pre-period forecast is a distribution with both
 * intervals and its model version, the Fund probabilities carry the BR-184 sentence, a Fund
 * scenario is shown with its own intervals, and the ledger keeps its missed periods and scores
 * calibration over the model's fixed N whatever window is shown.
 */
import type { ReactElement } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { getForecastLedger, getFuelPriceForecast } from '../../lib/v1/forecast';
import { createForecastHandlers } from '../../mocks/forecast';
import ForecastLedgerPage, { calibrationStats } from './ForecastLedgerPage';
import { distributionRows, ledgerChartRows, waterfallRows } from './FuelCharts';
import FuelForecastPage, { FUND_DISCLAIMER } from './FuelForecastPage';
import { hasInterval } from './fuelParts';

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

describe('FuelForecastPage', () => {
  it('shows the distribution with both intervals, the model version and the Fund sentence', async () => {
    show(<FuelForecastPage />);
    expect(await screen.findByText(/Dự báo giá trần · kỳ 09\/7\/2026/)).toBeInTheDocument();
    expect(screen.getByText('Số dự báo minh hoạ')).toBeInTheDocument();
    expect(screen.getByText(/formula_mc v2 · 10\.000 lần mô phỏng/)).toBeInTheDocument();
    expect(screen.getByText('19.730')).toBeInTheDocument();
    expect(screen.getByText('+180 đ/lít so kỳ trước · minh hoạ')).toBeInTheDocument();
    expect(screen.getByText('72%')).toBeInTheDocument();
    expect(screen.getByText('Khoảng 80%: 19.640 – 20.190')).toBeInTheDocument();
    expect(screen.getByText('Khoảng 95%: 19.480 – 20.350')).toBeInTheDocument();

    const fund = screen.getByRole('table', { name: 'Xác suất theo mức Quỹ' });
    expect(within(fund).getAllByRole('row')).toHaveLength(5);
    expect(screen.getByText(FUND_DISCLAIMER)).toBeInTheDocument();
    expect(FUND_DISCLAIMER).toBe(
      'Quyết định Quỹ do liên Bộ quyết; mô hình chỉ ước xác suất theo các kỳ đã qua.',
    );

    expect(screen.getByText('thue_nk_bq_xang')).toBeInTheDocument();
    expect(screen.getByText('5/7 ngày đã biết · 2 ngày mô phỏng (03/7–08/7)')).toBeInTheDocument();

    const recent = await screen.findByRole('table', { name: 'Sổ 5 kỳ gần nhất' });
    expect(within(recent).getAllByRole('row')).toHaveLength(6);
    expect(within(recent).getByText('−460')).toBeInTheDocument();
    expect(
      screen.getByText('Độ phủ 20 kỳ: 80% → 16/20 · 95% → 19/20 · minh hoạ'),
    ).toBeInTheDocument();
  });

  it('switches fuel by tab, opens the ledger and the cited document', async () => {
    show(<FuelForecastPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Diesel 0,05S' }));
    expect(navigate).toHaveBeenCalledWith(
      'analysis',
      'FUEL_FORECAST',
      'fpf-2026-07-09-diesel-005s',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Mở sổ' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_LEDGER', 'fs-ceiling-e5ron92');
    fireEvent.click(screen.getByRole('button', { name: 'NQ 34/2026/NQ-CP' }));
    expect(navigate).toHaveBeenCalledWith('data', 'DOCUMENT', 'doc-nq-34-2026');
  });

  it('runs a Fund scenario with its own intervals and clears it', async () => {
    show(<FuelForecastPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Kịch bản Quỹ' }));
    fireEvent.click(screen.getByRole('radio', { name: /Trích 300 đ\/lít/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Chạy kịch bản' }));
    const notice = await screen.findByText(/Kịch bản Quỹ Trích 300 đ\/lít: trung vị 20\.040/);
    expect(notice.textContent).toMatch(
      /khoảng 80% 19\.810 – 20\.280 · khoảng 95% 19\.670 – 20\.420/,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ kịch bản' }));
    await waitFor(() => expect(screen.queryByText(/Kịch bản Quỹ Trích/)).not.toBeInTheDocument());
  });

  it('follows the publication schedule', async () => {
    show(<FuelForecastPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Theo dõi lịch' }));
    expect(await screen.findByRole('button', { name: 'Đang theo dõi lịch' })).toBeInTheDocument();
  });

  it('does not draw a forecast whose interval is missing', async () => {
    routeId = 'fpf-2026-07-09-e5ron92';
    const full = await getFuelPriceForecast(routeId);
    server.use(
      http.get('*/v1/fuel-price-forecasts/:id', () =>
        HttpResponse.json({ ...full, distribution: { ...full.distribution, lo95: Number.NaN } }),
      ),
    );
    show(<FuelForecastPage />);
    expect(await screen.findByText(/thiếu khoảng tin cậy nên không hiển thị/)).toBeInTheDocument();
    expect(screen.queryByText(/Trung vị kỳ/)).not.toBeInTheDocument();
  });
});

describe('fuel forecast shapes', () => {
  it('accepts only nested, finite intervals', () => {
    const ok = { lo95: 1, lo80: 2, median: 3, hi80: 4, hi95: 5 };
    expect(hasInterval(ok)).toBe(true);
    expect(hasInterval({ ...ok, hi95: Number.NaN })).toBe(false);
    expect(hasInterval({ ...ok, lo80: 0.5, lo95: 1 })).toBe(false);
  });

  it('fills the 80/95% regions exactly between their bounds', async () => {
    const f = await getFuelPriceForecast('fpf-2026-07-09-e5ron92');
    const rows = distributionRows(f);
    const in80 = rows.filter((r) => r.in80 !== undefined).map((r) => r.price);
    expect(in80[0]).toBe(19640);
    expect(in80[in80.length - 1]).toBe(20190);
    expect(
      distributionRows({ ...f, distribution: { ...f.distribution, hi80: Number.NaN } }),
    ).toEqual([]);
  });

  it('adds the decomposition steps up to the expected ceiling', async () => {
    const f = await getFuelPriceForecast('fpf-2026-07-09-e5ron92');
    const rows = waterfallRows(f);
    expect(rows.map((r) => r.label)).toEqual(['19.730', '+195', '+14', '0', '−24', '19.915']);
    const quy = rows[4];
    // The Fund step goes down from the running total 19.939 to 19.915.
    expect(quy.base + f.decomposition.axis_from).toBe(19915);
    expect(rows[5].bar + f.decomposition.axis_from).toBe(f.decomposition.expected);
  });

  it('reads the ledger chart oldest first', async () => {
    const l = await getForecastLedger('fs-ceiling-e5ron92');
    const rows = ledgerChartRows(l.rows);
    expect(rows[0].label).toBe('19/02');
    expect(rows[rows.length - 1].label).toBe('02/07');
    expect(rows.filter((r) => r.missed).map((r) => r.label)).toEqual(['09/04', '11/06']);
  });
});

describe('ForecastLedgerPage', () => {
  it('scores calibration over the fixed N and keeps the missed periods', async () => {
    show(<ForecastLedgerPage />);
    expect(await screen.findByText(/Sổ dự báo · Giá trần E5RON92/)).toBeInTheDocument();
    expect(screen.getByText('16/20 kỳ trong khoảng')).toBeInTheDocument();
    expect(screen.getByText('19/20 kỳ trong khoảng')).toBeInTheDocument();
    expect(screen.getByText('134 đ/lít')).toBeInTheDocument();
    expect(screen.getByText('−38%')).toBeInTheDocument();
    expect(screen.getByText('ngoài 95% hoặc sai > 300 đ/lít')).toBeInTheDocument();
    expect(screen.getByText('N = 20 kỳ · cố định theo mô hình')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Kỳ' }).textContent).toContain(
      'Kỳ: 20 kỳ gần nhất · 19/02 – 02/07/2026',
    );

    const table = screen.getByRole('table', { name: 'Sổ dự báo · mới nhất trước' });
    expect(within(table).getAllByRole('row')).toHaveLength(21);

    fireEvent.click(screen.getByRole('checkbox', { name: 'Chỉ kỳ trượt' }));
    const missed = within(screen.getByRole('table', { name: 'Sổ dự báo · mới nhất trước' }));
    expect(missed.getAllByRole('row')).toHaveLength(3);
    expect(missed.getByText('EB-0031')).toBeInTheDocument();
    expect(missed.getByText('Quỹ chi không dự kiến 300 đ/lít')).toBeInTheDocument();
    expect(missed.getByText('Không / Không')).toBeInTheDocument();
  });

  it('shows a shorter window without changing the calibration', async () => {
    routeId = 'fs-ceiling-e5ron92';
    show(<ForecastLedgerPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Kỳ' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '10 kỳ gần nhất' }));
    await waitFor(() =>
      expect(
        within(screen.getByRole('table', { name: 'Sổ dự báo · mới nhất trước' })).getAllByRole(
          'row',
        ),
      ).toHaveLength(11),
    );
    expect(screen.getByText('16/20 kỳ trong khoảng')).toBeInTheDocument();
  });

  it('switches fuel from the menu', async () => {
    show(<ForecastLedgerPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Mặt hàng' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Mazut' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_LEDGER', 'fs-ceiling-mazut');
  });

  it('says when the model is no better than the naive forecast', async () => {
    const l = await getForecastLedger('fs-ceiling-e5ron92');
    const stats = calibrationStats({
      ...l,
      calibration: { ...l.calibration, crps_vs_naive: 0.05 },
    });
    expect(stats[3]).toMatchObject({ value: '+5%', tone: 'error' });
  });
});
