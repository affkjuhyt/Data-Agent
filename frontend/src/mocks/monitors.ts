import { http, HttpResponse } from 'msw';
import type { Monitor, MonitorDraft, MonitorOptions, MonitorPreview } from '../lib/v1/monitors';

/*
 * Theo dõi (P08, UC-08-01) for the dev mock: the 14 monitors of "P08 · Theo dõi · Danh sách"
 * (2270:15950), the choices of "Tạo theo dõi" (2270:16233) — NPL_RATIO has 90 days, OVERDUE_30
 * only 12, so Bất thường waits on it (2272:16634) — and a 90-day preview that rises past the
 * threshold in DH Bắc Bộ only.
 */
const at = (daysAgo: number, hh: number, mm: number) => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
};

const monitor = (m: Partial<Monitor> & Pick<Monitor, 'id' | 'name' | 'metric_text'>): Monitor => ({
  rule_kind: 'threshold',
  condition_text: '',
  state: 'normal',
  muted_until: null,
  last_fired_at: null,
  last_alert_id: null,
  channels_text: 'Teams',
  owner_name: 'Đỗ Hải Yến',
  owner_is_me: true,
  last_checked_at: at(0, 10, 42),
  ...m,
});

function monitorsFixture(): Monitor[] {
  return [
    monitor({
      id: 'mon-npl-north',
      name: 'Nợ xấu bán lẻ · DH Bắc Bộ',
      metric_text: 'NPL_RATIO v4 · tách theo vùng',
      condition_text: '> 2,0% trong 1 ngày',
      state: 'alerting',
      last_fired_at: at(0, 7, 15),
      last_alert_id: 'alert-npl-north',
      channels_text: 'Teams, email',
    }),
    monitor({
      id: 'mon-npl-red-river',
      name: 'Nợ xấu bán lẻ · ĐB sông Hồng',
      metric_text: 'NPL_RATIO v4 · tách theo vùng',
      condition_text: '> 2,0% trong 1 ngày',
      state: 'early_warning',
      last_fired_at: at(0, 7, 15),
      last_alert_id: 'alert-npl-red-river',
    }),
    monitor({
      id: 'mon-card-balance',
      name: 'Dư nợ thẻ tín dụng',
      metric_text: 'LOAN_BALANCE · thẻ',
      rule_kind: 'change_vs_previous',
      condition_text: 'giảm > 5% so với tuần trước',
      state: 'early_warning',
      last_fired_at: at(1, 18, 0),
      last_alert_id: 'alert-card-balance',
      owner_name: 'Phạm Đức Long',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-recovery',
      name: 'Tỷ lệ thu hồi nợ',
      metric_text: 'RECOVERY_RATE',
      rule_kind: 'seasonal_anomaly',
      condition_text: 'bất thường · độ nhạy vừa',
      last_fired_at: at(8, 9, 0),
      last_alert_id: 'alert-recovery-closed',
      channels_text: 'Email',
    }),
    monitor({
      id: 'mon-casa',
      name: 'CASA toàn hàng',
      metric_text: 'CASA_RATIO v3',
      rule_kind: 'forecast',
      condition_text: '< 20% · dự báo 30 ngày',
      channels_text: 'Email',
      owner_name: 'Phạm Đức Long',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-overdue-30',
      name: 'Nợ quá hạn 30+ · tiêu dùng',
      metric_text: 'OVERDUE_30 · chưa chứng nhận',
      condition_text: '> 4,5%',
      state: 'insufficient_data',
      channels_text: 'Chỉ trong ứng dụng',
    }),
    monitor({
      id: 'mon-cost-of-risk',
      name: 'Chi phí rủi ro',
      metric_text: 'COST_OF_RISK',
      rule_kind: 'change_vs_previous',
      condition_text: 'tăng > 0,1 điểm so với tháng trước',
      state: 'muted',
      muted_until: at(-1, 0, 0),
      last_fired_at: at(13, 8, 0),
      channels_text: 'Teams, Zalo',
      owner_name: 'Phạm Đức Long',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-npl-south',
      name: 'Nợ xấu bán lẻ · ĐB sông Cửu Long',
      metric_text: 'NPL_RATIO v4 · tách theo vùng',
      condition_text: '> 2,0% trong 1 ngày',
      state: 'alerting',
      last_fired_at: at(2, 7, 15),
      last_alert_id: 'alert-npl-south',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-npl-central',
      name: 'Nợ xấu bán lẻ · Bắc Trung Bộ',
      metric_text: 'NPL_RATIO v4 · tách theo vùng',
      condition_text: '> 2,0% trong 1 ngày',
      state: 'alerting',
      last_fired_at: at(3, 7, 15),
      last_alert_id: 'alert-npl-central',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-deposit',
      name: 'Huy động khách hàng cá nhân',
      metric_text: 'DEPOSIT_BALANCE',
      rule_kind: 'change_vs_previous',
      condition_text: 'giảm > 3% so với tuần trước',
      last_fired_at: at(20, 18, 0),
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-mortgage',
      name: 'Giải ngân mua nhà',
      metric_text: 'DISBURSEMENT · nhà ở',
      rule_kind: 'seasonal_anomaly',
      condition_text: 'bất thường · độ nhạy thấp',
    }),
    monitor({
      id: 'mon-fee',
      name: 'Thu phí dịch vụ thẻ',
      metric_text: 'FEE_INCOME · thẻ',
      rule_kind: 'seasonal_anomaly',
      condition_text: 'bất thường · độ nhạy vừa',
      owner_is_me: false,
    }),
    monitor({
      id: 'mon-npl-sme',
      name: 'Nợ xấu SME',
      metric_text: 'NPL_RATIO v4 · SME',
      condition_text: '> 3,0% trong 1 ngày',
      channels_text: 'Teams, email',
    }),
    monitor({
      id: 'mon-ltv',
      name: 'LTV bình quân vay nhà',
      metric_text: 'LTV_AVG',
      condition_text: '> 70%',
      state: 'muted',
      muted_until: at(-5, 0, 0),
      owner_name: 'Phạm Đức Long',
      owner_is_me: false,
    }),
  ];
}

function monitorOptionsFixture(): MonitorOptions {
  return {
    metrics: [
      {
        id: 'metric-npl-ratio',
        label: 'Nợ xấu (%) · NPL_RATIO v4 · đã chứng nhận',
        certified: true,
        unit: '%',
        history_days: 900,
      },
      {
        id: 'metric-overdue-30',
        label: 'Nợ quá hạn 30+ (%) · OVERDUE_30 · chưa chứng nhận',
        certified: false,
        unit: '%',
        history_days: 12,
      },
      {
        id: 'metric-casa',
        label: 'CASA (%) · CASA_RATIO v3 · đã chứng nhận',
        certified: true,
        unit: '%',
        history_days: 400,
      },
    ],
    scopes: [
      { value: 'segment=retail', label: 'Phân khúc = Bán lẻ' },
      { value: 'segment=sme', label: 'Phân khúc = SME' },
      { value: 'all', label: 'Toàn hàng' },
    ],
    splits: [
      {
        value: 'region',
        label: 'Vùng (6 nhóm)',
        groups: [
          'DH Bắc Bộ',
          'ĐB sông Hồng',
          'Bắc Trung Bộ',
          'Tây Nguyên',
          'Đông Nam Bộ',
          'ĐB sông Cửu Long',
        ],
      },
      { value: 'none', label: 'Không tách', groups: ['Toàn bộ'] },
    ],
    recipients: [
      { value: 'me,long', label: 'Tôi, Phạm Đức Long' },
      { value: 'me', label: 'Tôi' },
      { value: 'team-risk', label: 'Nhóm Rủi ro tín dụng' },
    ],
    channels: [
      { value: 'teams,email', label: 'Teams, email' },
      { value: 'teams', label: 'Teams' },
      { value: 'email', label: 'Email' },
      { value: 'zalo', label: 'Zalo' },
    ],
    analysis_cost_text: 'Khoảng 2–4 phút và 9.000 đ mỗi lần.',
  };
}

// Ninety days of a gently waving ratio; DH Bắc Bộ climbs past 2,0% on the last three.
const CLIMB = [1.72, 1.85, 1.95, 2.08, 2.18, 2.26];
function series(group: string) {
  return Array.from({ length: 90 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (89 - i));
    const wave = 1.62 + 0.1 * Math.sin(i / 9) + 0.03 * Math.sin(i / 3);
    const value = group === 'DH Bắc Bộ' && i >= 84 ? CLIMB[i - 84] : wave;
    return { date: d.toISOString().slice(0, 10), value: Math.round(value * 100) / 100 };
  });
}

const WORD = ['không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu'];
const fmt = (n: number) => n.toFixed(1).replace('.', ',');

function preview(draft: MonitorDraft & { group: string }): MonitorPreview {
  const metric = monitorOptionsFixture().metrics.find((m) => m.id === draft.metric_id);
  const needs = { seasonal_anomaly: 30, forecast: 60 }[draft.rule_kind as string] ?? 7;
  if (!metric || metric.history_days < needs)
    return {
      series: null,
      thresholds: [],
      fires: 0,
      periods: 0,
      text: 'Chưa đủ dữ liệu để xem trước.',
      note: null,
    };
  const points = series(draft.group);
  const value = Number(draft.rule.value ?? 2);
  const early = draft.rule.early === undefined ? null : Number(draft.rule.early);
  const above = draft.rule.direction !== 'below';
  const crossed = (v: number) => (above ? v > value : v < value);
  const fires = draft.rule_kind === 'threshold' ? points.filter((p) => crossed(p.value)).length : 0;
  // The note speaks of the other groups: did any of them ever cross the early warning?
  const othersCross =
    early !== null &&
    series(draft.group === 'Toàn bộ' ? 'Toàn bộ' : 'other').some((p) =>
      above ? p.value > early : p.value < early,
    );
  return {
    series: points,
    thresholds: [
      { label: `Ngưỡng ${fmt(value)}%`, value, tone: 'error' },
      ...(early !== null
        ? [{ label: `Cảnh báo sớm ${fmt(early)}%`, value: early, tone: 'warning' as const }]
        : []),
    ],
    fires,
    periods: points.length,
    text: fires
      ? `Nếu bật từ 90 ngày trước, theo dõi này sẽ cảnh báo ${fires} lần, cả ${WORD[fires] ?? fires} trong 6 ngày gần đây và chỉ ở ${draft.group}.`
      : `Nếu bật từ 90 ngày trước, theo dõi này chưa cảnh báo lần nào ở ${draft.group}.`,
    note:
      early !== null && !othersCross
        ? `Các vùng khác chưa từng vượt ${fmt(early)}%, nên ngưỡng này ít gây báo nhầm.`
        : null,
  };
}

export function createMonitorHandlers() {
  let monitors = monitorsFixture();
  return [
    http.get('*/v1/monitors/options', () => HttpResponse.json(monitorOptionsFixture())),
    http.get('*/v1/monitors', () =>
      HttpResponse.json({ items: monitors, next_cursor: null, total: monitors.length }),
    ),
    http.post('*/v1/monitors\\:preview', async ({ request }) =>
      HttpResponse.json(preview((await request.json()) as MonitorDraft & { group: string })),
    ),
    http.post('*/v1/monitors', async ({ request }) => {
      const draft = (await request.json()) as MonitorDraft;
      const metric = monitorOptionsFixture().metrics.find((m) => m.id === draft.metric_id);
      const created = monitor({
        id: `mon-new-${monitors.length}`,
        name: metric?.label.split(' · ')[0] ?? 'Theo dõi mới',
        metric_text: metric?.label.split(' · ')[1] ?? '',
        rule_kind: draft.rule_kind,
        condition_text: String(draft.rule.text ?? ''),
        channels_text: metric?.certified ? draft.recipients.channels : 'Chỉ trong ứng dụng',
        last_checked_at: new Date().toISOString(),
      });
      monitors = [created, ...monitors];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.patch('*/v1/monitors/:id', async ({ params, request }) => {
      const body = (await request.json()) as { muted_until: string | null };
      const found = monitors.find((m) => m.id === params.id);
      if (!found)
        return HttpResponse.json(
          {
            title: 'not_found',
            status: 404,
            code: 'not_found',
            detail: 'Không tìm thấy theo dõi.',
          },
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        );
      const updated: Monitor = {
        ...found,
        muted_until: body.muted_until,
        state: body.muted_until ? 'muted' : 'normal',
      };
      monitors = monitors.map((m) => (m.id === found.id ? updated : m));
      return HttpResponse.json(updated);
    }),
  ];
}
