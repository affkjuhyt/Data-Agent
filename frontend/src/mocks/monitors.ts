import { http, HttpResponse } from 'msw';
import type { Monitor, MonitorWire } from '../lib/v1/monitors';
import type { components } from '../lib/v1/schema';

type Schemas = components['schemas'];

/*
 * Theo dõi (P08, UC-08-01) for the dev mock: the 14 monitors of "P08 · Theo dõi · Danh sách"
 * (2270:15950) as MonitorView, with the names and words the frame draws added beside it (A3 has
 * no monitor name). "Tạo theo dõi" reads the metrics of the shared mock (GET /v1/metrics).
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
  noise_warning: null,
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
      condition_text: '< 20%',
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
      owner_name: 'Phạm Đức Long',
      owner_is_me: false,
    }),
  ];
}

const ME = '01920000-0000-7000-8000-00000000a5c1';

/** The row as the api sends it (MonitorView), the frame's words beside it. */
function wire(m: Monitor): MonitorWire {
  return {
    ...m,
    data_space_id: 'ds-rui-ro-tin-dung',
    owner_id: m.owner_is_me ? ME : 'u-long',
    agent_def_id: null,
    metric_id: 'metric-npl-ratio',
    scope_filter: {},
    rule: { text: m.condition_text },
    schedule_cron: '0 7 * * *',
    notify_mode: 'with_analysis',
    recipients: [],
    backtest: null,
    status: m.state === 'muted' ? 'paused' : 'active',
    paused_reason: null,
    created_at: at(30, 9, 0),
    updated_at: at(1, 9, 0),
  };
}

export function createMonitorHandlers() {
  let monitors = monitorsFixture();
  return [
    http.get('*/v1/monitors', () =>
      HttpResponse.json({ items: monitors.map(wire), next_page_token: '' }),
    ),
    http.post('*/v1/monitors', async ({ request }) => {
      const body = (await request.json()) as Schemas['MonitorCreate'];
      const now = new Date().toISOString();
      const created: MonitorWire = {
        id: `mon-new-${monitors.length}`,
        data_space_id: 'ds-rui-ro-tin-dung',
        owner_id: ME,
        agent_def_id: null,
        metric_id: body.metric_id,
        scope_filter: body.scope_filter ?? {},
        rule_kind: body.rule_kind,
        rule: body.rule,
        schedule_cron: body.schedule_cron,
        notify_mode: body.notify_mode ?? 'alert_only',
        recipients: body.recipients ?? [],
        backtest: { status: 'pending', periods: 12, requested_at: now },
        noise_warning: null,
        status: 'active',
        paused_reason: null,
        created_at: now,
        updated_at: now,
      };
      return HttpResponse.json(created, { status: 201 });
    }),
    http.patch('*/v1/monitors/:id', async ({ params, request }) => {
      const body = (await request.json()) as Schemas['MonitorPatch'];
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
        state: body.status === 'paused' ? 'muted' : body.status === 'active' ? 'normal' : found.state,
      };
      monitors = monitors.map((m) => (m.id === found.id ? updated : m));
      return HttpResponse.json(wire(updated));
    }),
  ];
}
