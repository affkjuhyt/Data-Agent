import { http, HttpResponse } from 'msw';
import type { MyHome } from '../lib/v1/home';

/*
 * GET /v1/me/home for the dev mock: the three followed metrics of "v3.1 · Trang chủ"
 * (2132:15338), a fourth for the mobile grid (2231:15114), and the morning summary.
 */
function homeFixture(): MyHome {
  return {
    metrics: [
      {
        metric_id: 'metric-npl-ratio',
        label: 'Nợ xấu bán lẻ',
        short_label: 'NPL bán lẻ',
        value: '1,84%',
        certified: true,
        delta: { text: '+0,22 điểm so với T8', direction: 'up', good: false },
        note: 'DH Bắc Bộ đóng góp 74%',
        series: [1.58, 1.6, 1.63, 1.62, 1.66, 1.66, 1.72, 1.84],
      },
      {
        metric_id: 'metric-loan-balance',
        label: 'Dư nợ cho vay',
        short_label: 'Tổng dư nợ',
        value: '182.430 tỷ',
        certified: true,
        delta: { text: '+1,1% tuần này', direction: 'up', good: true },
        note: 'Vay mua nhà tăng mạnh nhất',
        series: [170, 172, 173, 175, 176, 178, 180, 182],
      },
      {
        metric_id: 'metric-casa',
        label: 'Tỷ lệ CASA',
        short_label: 'Tỷ lệ CASA',
        value: '21,4%',
        certified: true,
        delta: { text: 'không đổi', direction: 'flat', good: true },
        note: 'Đúng kế hoạch quý',
        series: [20.8, 21, 21.1, 21.2, 21.3, 21.3, 21.6, 21.4],
      },
      {
        metric_id: 'metric-reconcile-gaps',
        label: 'Lệch số chờ xử lý',
        short_label: 'Lệch số chờ xử lý',
        value: '1',
        certified: false,
        delta: { text: 'hạn 17:00', direction: 'flat', good: false },
        note: '',
        series: [],
      },
    ],
    briefing: {
      title: 'Tóm tắt sáng nay',
      lines: [
        'NPL bán lẻ 1,84%, tăng 0,05 điểm, vẫn dưới ngưỡng cảnh báo 2%.',
        'Core banking và DWH lệch 1,8 tỷ dư nợ nhóm 3–5, đang chờ bạn chọn nguồn.',
        'Kỳ 09/2026 chưa chốt được: lệch 4 khách hàng với sổ cái.',
      ],
    },
    data_note: 'Dữ liệu cập nhật 06:00 · Rủi ro tín dụng',
  };
}

export function createHomeHandlers() {
  return [http.get('*/v1/me/home', () => HttpResponse.json(homeFixture()))];
}
