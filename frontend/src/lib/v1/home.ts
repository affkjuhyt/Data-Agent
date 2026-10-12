import { v1Get } from './client';

/*
 * What Trang chủ shows about the viewer beyond their inbox and runs ("v3.1 · Trang chủ"
 * 2132:15338, "Mobile · Trang chủ" 2231:15114, UC-01-01, UC-08-05): the metrics they follow with
 * this period's value, how it moved and why in one line, a short series; and the morning summary
 * the agents wrote. PROPOSAL: GET /v1/me/home (docs/v3/05-ui-v3.md §4).
 */
export interface MyMetric {
  metric_id: string;
  label: string;
  /** Short label for the mobile 2×2 grid ("NPL bán lẻ"). */
  short_label: string;
  value: string;
  certified: boolean;
  delta: { text: string; direction: 'up' | 'down' | 'flat'; good: boolean; warning?: boolean };
  /** "DH Bắc Bộ đóng góp 74%" */
  note: string;
  /** The last periods, oldest first, for the small bars. */
  series: number[];
}

export interface MyHome {
  metrics: MyMetric[];
  briefing: { lines: string[]; title: string } | null;
  /** "Dữ liệu cập nhật 06:00 · Rủi ro tín dụng" */
  data_note: string;
}

export const getMyHome = async () => v1Get<MyHome>('/me/home');
