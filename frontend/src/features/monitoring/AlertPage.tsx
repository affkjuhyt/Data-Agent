import { useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  assignAlert,
  closeAlert,
  disputeAlertAnalysis,
  getAlert,
  investigateAlert,
  listAlerts,
  reopenAlert,
  type Alert,
} from '../../lib/v1/alerts';
import { listUsers } from '../../lib/v1/api';
import { muteMonitor } from '../../lib/v1/monitors';
import { useV1 } from '../../lib/v1/useV1';
import { whenText } from './whenText';

/*
 * "P08 · Cảnh báo · Chi tiết" (2271:16093, UC-08-02): one alert — the 30 days around it with the
 * days past the threshold shaded, the cause the Agent Phân tích attached and what each factor
 * contributed, and on the right its details, what happened since and how it ends. The person
 * mutes the monitor for a day, hands the alert to someone else, or opens an investigation. Closing
 * it as a false alarm needs a note the agent learns from ("Đã đóng do báo nhầm", 2272:16850); an
 * alert that fired more than twice needs a note however it is closed (BR-124). Opened from the
 * rail without an id, the page shows the newest open alert.
 */
type Dialog = 'assign' | 'resolve' | 'false_positive' | 'dispute' | null;

const STATUS = {
  open: { label: 'Đang cảnh báo', tone: 'error' },
  ack: { label: 'Đã xác nhận', tone: 'warning' },
  assigned: { label: 'Đã giao', tone: 'info' },
  closed: { label: 'Đã đóng', tone: 'idle' },
} as const;
const fmt = (n: number, digits = 1) => n.toFixed(digits).replace('.', ',');
const signed = (n: number) => `+${fmt(n, 2)}`;

export default function AlertPage() {
  const { routeId } = useAppState();
  const open = useV1(routeId ? null : () => listAlerts('open'), [routeId]);
  const id = routeId || open.data?.items[0]?.id;
  const alert = useV1(id ? () => getAlert(id) : null, [id]);
  if (!routeId && open.data && open.data.items.length === 0)
    return (
      <div className="px-da-3xl pt-da-2xl">
        <EmptyState
          title="Không có cảnh báo đang mở"
          description="Mọi theo dõi đang bình thường."
        />
      </div>
    );
  return (
    <AsyncRegion state={alert} what="cảnh báo">
      {(a) => <AlertBody key={a.id} alert={a} onChanged={alert.reload} />}
    </AsyncRegion>
  );
}

function AlertBody({ alert, onChanged }: { alert: Alert; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState<string | null>(null);
  const [showSql, setShowSql] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const users = useV1(dialog === 'assign' ? listUsers : null, [dialog === 'assign']);
  const closed = alert.status === 'closed';
  const status =
    closed && alert.close_kind === 'false_positive'
      ? { label: 'Đã đóng · báo nhầm', tone: 'idle' as const }
      : STATUS[alert.status];

  const act = async (key: string, call: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      setDialog(null);
      setNote('');
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const close = (kind: 'resolved' | 'false_positive') => {
    if ((kind === 'false_positive' || alert.occurrences > 2) && !note.trim()) {
      setError(
        kind === 'false_positive'
          ? 'Ghi vì sao đây là báo nhầm; agent học từ ghi chú này.'
          : 'Cảnh báo lặp hơn 2 lần cần ghi chú khi đóng (BR-124).',
      );
      return;
    }
    act(
      kind === 'resolved' ? 'resolve' : kind,
      () => closeAlert(alert.id, note.trim(), kind),
      kind === 'false_positive' ? `Đã đóng ${alert.code} do báo nhầm` : `Đã đóng ${alert.code}`,
    );
  };

  const investigate = async () => {
    setBusy('investigate');
    setError(undefined);
    try {
      const { workspace_id } = await investigateAlert(alert.id);
      navigate('analysis', 'SESSIONS', workspace_id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };

  const mute = () => {
    const until = new Date(Date.now() + 24 * 3600_000).toISOString();
    act(
      'mute',
      () => muteMonitor(alert.monitor_id, until),
      `Đã tắt tiếng ${alert.monitor_name} 24 giờ`,
    );
  };

  const dialogOpen = (d: Dialog) => {
    setError(undefined);
    setNote('');
    setDialog(d);
  };

  const total = alert.analysis?.factors.reduce((s, f) => s + f.contribution, 0) ?? 0;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Cảnh báo', alert.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {alert.title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={`Bắt đầu ${whenText(alert.fired_at)} · theo dõi “${alert.monitor_name}” · đã gửi ${alert.delivered_text}`}
        actions={
          closed ? (
            <Button
              loading={busy === 'reopen'}
              disabled={busy !== null}
              onClick={() => act('reopen', () => reopenAlert(alert.id), `Đã mở lại ${alert.code}`)}
            >
              Mở lại
            </Button>
          ) : (
            <>
              <Button
                kind="ghost"
                loading={busy === 'mute'}
                disabled={busy !== null}
                onClick={mute}
              >
                Tắt tiếng 24 giờ
              </Button>
              <Button disabled={busy !== null} onClick={() => dialogOpen('assign')}>
                Giao cho người khác
              </Button>
              <Button
                kind="primary"
                loading={busy === 'investigate'}
                disabled={busy !== null}
                onClick={investigate}
              >
                Mở điều tra
              </Button>
            </>
          )
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col px-da-3xl pb-da-2xl">
          {error && !dialog && (
            <div className="pt-da-lg">
              <Notice tone="error">Không thực hiện được: {error}</Notice>
            </div>
          )}
          {alert.closed && (
            <section className="flex flex-col gap-da-xs border-b border-border py-da-lg">
              <p className="text-foreground da-body">
                {alert.closed.by} đóng lúc {whenText(alert.closed.at).replace(' hôm nay', '')}
                {alert.closed.note ? `: “${alert.closed.note}”` : '.'}
              </p>
              {alert.closed.agent_note && (
                <p className="text-muted-foreground da-body-small">{alert.closed.agent_note}</p>
              )}
            </section>
          )}
          {/* PROPOSAL: the series behind the alert; without it the api's summary says what changed. */}
          {alert.chart ? (
            <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
              <div className="flex items-baseline gap-da-md">
                <h2 className="text-foreground da-heading-3">Giá trị 30 ngày</h2>
                <span className="ml-auto text-muted-foreground da-caption">
                  {alert.chart.metric_label} · {alert.chart.cutoff_text}
                </span>
              </div>
              <AlertChart chart={alert.chart} />
            </section>
          ) : (
            !alert.analysis &&
            alert.summary && (
              <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
                <h2 className="text-foreground da-heading-3">Tóm tắt</h2>
                <p className="whitespace-pre-line text-foreground da-body">{alert.summary}</p>
              </section>
            )
          )}
          {alert.analysis && (
            <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
              <div className="flex items-baseline gap-da-md">
                <h2 className="text-foreground da-heading-3">Nguyên nhân · Agent Phân tích</h2>
                <span className="ml-auto text-muted-foreground da-caption">
                  {alert.analysis.finished_text}
                </span>
              </div>
              <p className="text-foreground da-body">{alert.analysis.summary}</p>
              <table className="w-full table-fixed border-collapse da-body-small">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="py-da-sm font-normal">Yếu tố</th>
                    <th className="w-[106px] py-da-sm font-normal">Đóng góp</th>
                    <th className="w-[260px] py-da-sm font-normal">
                      <span className="sr-only">Tỷ phần</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {alert.analysis.factors.map((f) => (
                    <tr key={f.label} className="border-b border-border text-foreground">
                      <td className="py-[9px]">{f.label}</td>
                      <td className="py-[9px] da-mono">{signed(f.contribution)}</td>
                      <td className="py-[9px]">
                        <span className="block h-2 w-full rounded-full bg-subtle">
                          <span
                            className="block h-full rounded-full bg-warning"
                            style={{ width: `${total ? (f.contribution / total) * 100 : 0}%` }}
                          />
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex flex-wrap gap-da-lg da-body-small">
                <Tooltip content="Bảng chi tiết khoản vay đang được làm.">
                  <button type="button" aria-disabled="true" className="text-primary opacity-60">
                    Xem {alert.analysis.record_count.toLocaleString('vi-VN')} khoản vay
                  </button>
                </Tooltip>
                <button
                  type="button"
                  aria-expanded={showSql}
                  onClick={() => setShowSql((v) => !v)}
                  className="text-primary hover:underline"
                >
                  {showSql ? 'Ẩn truy vấn' : 'Xem truy vấn'}
                </button>
                <button
                  type="button"
                  onClick={() => dialogOpen('dispute')}
                  className="text-primary hover:underline"
                >
                  Báo phân tích này sai
                </button>
              </div>
              {showSql && (
                <pre className="overflow-x-auto rounded-md bg-subtle p-da-md text-foreground da-mono">
                  {alert.analysis.sql}
                </pre>
              )}
            </section>
          )}
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col border-l border-border bg-background px-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <Side title="Chi tiết">
            <dl className="flex flex-col gap-da-sm da-body-small">
              {alert.details.map((d) => (
                <div key={d.label} className="flex gap-da-md">
                  <dt className="w-[110px] shrink-0 text-muted-foreground">{d.label}</dt>
                  <dd className={d.mono ? 'text-foreground da-mono' : 'text-foreground'}>
                    {d.value}
                  </dd>
                </div>
              ))}
            </dl>
          </Side>
          <Side title="Hoạt động">
            <ol className="flex flex-col gap-da-sm da-body-small">
              {alert.activity.map((a, i) => (
                <li key={`${a.at}-${i}`} className="flex gap-da-lg">
                  <span className="w-10 shrink-0 text-muted-foreground da-mono">{a.at}</span>
                  <span className="text-foreground">{a.text}</span>
                </li>
              ))}
            </ol>
          </Side>
          <Side title="Kết thúc cảnh báo">
            <p className="text-muted-foreground da-caption">{alert.end_rule_text}</p>
            {!closed && (
              <div className="flex flex-wrap gap-da-sm">
                <Button size="sm" disabled={busy !== null} onClick={() => dialogOpen('resolve')}>
                  Đánh dấu đã xử lý
                </Button>
                <Button
                  size="sm"
                  kind="ghost"
                  disabled={busy !== null}
                  onClick={() => dialogOpen('false_positive')}
                >
                  Đóng do báo nhầm
                </Button>
              </div>
            )}
          </Side>
        </aside>
      </div>
      <ConfirmDialog
        open={dialog === 'assign'}
        title={`Giao ${alert.code} cho người khác`}
        confirmLabel="Giao"
        tone="primary"
        cancelLabel="Quay lại"
        loading={busy === 'assign'}
        error={dialog === 'assign' ? error : undefined}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          const user = users.data?.items.find((u) => u.id === assignee);
          if (!user) {
            setError('Chọn người nhận.');
            return;
          }
          act(
            'assign',
            () => assignAlert(alert.id, user.id),
            `Đã giao ${alert.code} cho ${user.display_name}`,
          );
        }}
      >
        <Select
          label="Người nhận"
          value={assignee}
          placeholder="Chọn người"
          onChange={setAssignee}
          options={(users.data?.items ?? [])
            .filter((u) => u.kind === 'person')
            .map((u) => ({ value: u.id, label: u.display_name }))}
        />
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === 'resolve' || dialog === 'false_positive' || dialog === 'dispute'}
        title={
          dialog === 'false_positive'
            ? `Đóng ${alert.code} do báo nhầm?`
            : dialog === 'dispute'
              ? 'Báo phân tích này sai'
              : `Đánh dấu ${alert.code} đã xử lý?`
        }
        confirmLabel={dialog === 'dispute' ? 'Gửi' : 'Đóng cảnh báo'}
        tone="primary"
        cancelLabel="Quay lại"
        loading={busy === dialog}
        error={dialog ? error : undefined}
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          if (dialog === 'dispute') {
            if (!note.trim()) {
              setError('Ghi phân tích sai ở đâu.');
              return;
            }
            act(
              'dispute',
              () => disputeAlertAnalysis(alert.id, note.trim()),
              'Đã gửi; phân tích này vào bộ đánh giá của agent',
            );
          } else if (dialog === 'resolve') close('resolved');
          else if (dialog === 'false_positive') close('false_positive');
        }}
      >
        <div className="flex flex-col gap-da-sm">
          <p>
            {dialog === 'false_positive'
              ? 'Agent ghi nhớ điều bạn viết để lần sau giải thích đúng hơn; ngưỡng theo dõi giữ nguyên.'
              : dialog === 'dispute'
                ? 'Phân tích sai đi vào bộ đánh giá của Agent Phân tích.'
                : 'Cảnh báo đóng lại; theo dõi vẫn chạy.'}
          </p>
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">
              {dialog === 'resolve' && alert.occurrences <= 2 ? 'Ghi chú' : 'Ghi chú (bắt buộc)'}
            </span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
            />
          </label>
        </div>
      </ConfirmDialog>
    </div>
  );
}

function AlertChart({ chart }: { chart: NonNullable<Alert['chart']> }) {
  const { series, threshold, breach_from, unit } = chart;
  const last = series[series.length - 1]?.date;
  return (
    <div
      className="h-[160px] w-full"
      role="img"
      aria-label={`Giá trị 30 ngày; vượt ${fmt(threshold)}${unit} từ ${breach_from.slice(8, 10)}/${breach_from.slice(5, 7)}`}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: 800, height: 160 }}
      >
        <LineChart data={series} margin={{ top: 8, right: 140, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="date" hide />
          <YAxis
            stroke="var(--muted-foreground)"
            fontSize={11}
            axisLine={false}
            tickLine={false}
            width={40}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => `${fmt(v)}${unit}`}
          />
          <ReferenceArea x1={breach_from} x2={last} fill="var(--error-subtle)" fillOpacity={1} />
          <ReferenceLine y={threshold} stroke="var(--error)" strokeDasharray="6 4" />
          <Line
            dataKey="value"
            stroke="var(--primary)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Side({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}
