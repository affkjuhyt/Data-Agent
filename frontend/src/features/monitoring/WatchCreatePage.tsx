import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { Toggle } from '../../design-system/Toggle';
import { Tooltip } from '../../design-system/Tooltip';
import {
  createMonitor,
  getMonitorOptions,
  previewMonitor,
  type MonitorDraft,
  type MonitorOptions,
  type MonitorPreview,
  type RuleKind,
} from '../../lib/v1/monitors';
import { useV1 } from '../../lib/v1/useV1';
import { PreviewChart } from './PreviewChart';

/*
 * "P08 · Theo dõi · Tạo theo dõi" (2270:16233, UC-08-01): metric and scope, the rule, who hears of
 * it, and whether the Agent Phân tích attaches a cause. On the right the draft runs against the last
 * 90 days of one group, so the person sees how often it would have fired before turning it on; above
 * 30% of periods is noisy (BR-32). Bất thường learns a normal range from 30 days and Dự báo from 60;
 * a metric with less history cannot use them yet ("Tạo · Không đủ lịch sử", 2272:16634). An
 * uncertified metric alerts inside the app only.
 */
const KINDS: { value: RuleKind; label: string }[] = [
  { value: 'threshold', label: 'Ngưỡng' },
  { value: 'change_vs_previous', label: 'Thay đổi' },
  { value: 'seasonal_anomaly', label: 'Bất thường' },
  { value: 'forecast', label: 'Dự báo' },
];
const NEEDS_DAYS: Partial<Record<RuleKind, number>> = { seasonal_anomaly: 30, forecast: 60 };
const NOISY_SHARE = 0.3;
const PREVIEW_DELAY_MS = 300;
const DAILY = '0 7 * * *';

const opt = (pairs: [string, string][]) => pairs.map(([value, label]) => ({ value, label }));
const DIRECTIONS = opt([
  ['above', 'lớn hơn'],
  ['below', 'nhỏ hơn'],
]);
const WINDOWS = opt([
  ['1d', '1 ngày'],
  ['3d', '3 ngày'],
  ['7d', '7 ngày'],
]);
const CHANGE_DIRECTIONS = opt([
  ['up', 'tăng'],
  ['down', 'giảm'],
]);
const BASES = opt([
  ['week', 'tuần trước'],
  ['month', 'tháng trước'],
]);
const SENSITIVITY = opt([
  ['low', 'thấp'],
  ['medium', 'vừa'],
  ['high', 'cao'],
]);
const HORIZONS = opt([
  ['30d', '30 ngày'],
  ['90d', '90 ngày'],
]);

const parse = (s: string) => Number(s.replace(',', '.'));
const valid = (s: string) => s.trim() !== '' && Number.isFinite(parse(s));

export default function WatchCreatePage() {
  const options = useV1(getMonitorOptions, []);
  return (
    <AsyncRegion state={options} what="lựa chọn theo dõi">
      {(o) => <WatchForm options={o} />}
    </AsyncRegion>
  );
}

function WatchForm({ options }: { options: MonitorOptions }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [metricId, setMetricId] = useState(options.metrics[0]?.id ?? '');
  const [scope, setScope] = useState(options.scopes[0]?.value ?? '');
  const [split, setSplit] = useState(options.splits[0]?.value ?? '');
  const [kind, setKind] = useState<RuleKind>('threshold');
  const [direction, setDirection] = useState('above');
  const [value, setValue] = useState('2,0');
  const [window_, setWindow] = useState('1d');
  const [early, setEarly] = useState('1,9');
  const [recover, setRecover] = useState('1,8');
  const [change, setChange] = useState('5');
  const [changeDirection, setChangeDirection] = useState('down');
  const [base, setBase] = useState('week');
  const [sensitivity, setSensitivity] = useState('medium');
  const [horizon, setHorizon] = useState('30d');
  const [who, setWho] = useState(options.recipients[0]?.value ?? '');
  const [channels, setChannels] = useState(options.channels[0]?.value ?? '');
  const [withAnalysis, setWithAnalysis] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const metric = options.metrics.find((m) => m.id === metricId);
  const groups = options.splits.find((s) => s.value === split)?.groups ?? [];
  const [group, setGroup] = useState(groups[0] ?? '');
  const unit = metric?.unit ?? '';
  const needs = NEEDS_DAYS[kind];
  const lacking = metric && needs && metric.history_days < needs ? needs : null;
  const ready = new Date();
  if (lacking && metric) ready.setDate(ready.getDate() + (lacking - metric.history_days));

  const rule: Record<string, string | number> =
    kind === 'threshold'
      ? {
          direction,
          value: parse(value),
          window: window_,
          ...(valid(early) ? { early: parse(early) } : {}),
          ...(valid(recover) ? { recover: parse(recover) } : {}),
          text: `${direction === 'above' ? '>' : '<'} ${value}${unit} trong ${WINDOWS.find((w) => w.value === window_)?.label}`,
        }
      : kind === 'change_vs_previous'
        ? {
            direction: changeDirection,
            value: parse(change),
            base,
            text: `${changeDirection === 'up' ? 'tăng' : 'giảm'} > ${change}% so với ${BASES.find((b) => b.value === base)?.label}`,
          }
        : kind === 'seasonal_anomaly'
          ? {
              sensitivity,
              text: `bất thường · độ nhạy ${SENSITIVITY.find((s) => s.value === sensitivity)?.label}`,
            }
          : {
              direction: 'below',
              value: parse(value),
              horizon,
              text: `< ${value}${unit} · dự báo ${HORIZONS.find((h) => h.value === horizon)?.label}`,
            };
  const ruleOk =
    kind === 'threshold' || kind === 'forecast'
      ? valid(value)
      : kind === 'change_vs_previous'
        ? valid(change)
        : true;
  const draft: MonitorDraft = {
    metric_id: metricId,
    scope_filter: { scope, split },
    rule_kind: kind,
    rule,
    schedule_cron: DAILY,
    notify_mode: withAnalysis ? 'with_analysis' : 'alert_only',
    recipients: { who, channels: metric?.certified ? channels : 'in_app' },
  };
  const draftKey = JSON.stringify(draft);

  const [preview, setPreview] = useState<MonitorPreview>();
  // The preview follows the draft; an answer for an older draft is dropped.
  useEffect(() => {
    if (!ruleOk) return;
    let live = true;
    const timer = window.setTimeout(() => {
      previewMonitor(JSON.parse(draftKey) as MonitorDraft, group)
        .then((p) => live && setPreview(p))
        .catch(() => live && setPreview(undefined));
    }, PREVIEW_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [draftKey, group, ruleOk]);

  const noisy = preview?.periods ? preview.fires / preview.periods > NOISY_SHARE : false;
  const blocker = lacking
    ? `Chỉ số chưa đủ ${lacking} ngày dữ liệu cho điều kiện này.`
    : !ruleOk
      ? 'Nhập giá trị cho điều kiện.'
      : null;

  const create = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const created = await createMonitor(draft);
      toast({ tone: 'success', message: `Đã tạo theo dõi ${created.name}` });
      navigate('analysis', 'WATCH');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const createButton = (
    <Button kind="primary" loading={busy} disabled={busy || blocker !== null} onClick={create}>
      Tạo theo dõi
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Theo dõi', 'Tạo mới']}
        title="Tạo theo dõi"
        actions={
          <>
            <Button kind="ghost" onClick={() => navigate('analysis', 'WATCH')}>
              Huỷ
            </Button>
            {blocker ? <Tooltip content={blocker}>{createButton}</Tooltip> : createButton}
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex w-[560px] shrink-0 flex-col px-da-3xl pb-da-2xl max-[1023px]:w-full">
          {error && (
            <div className="pt-da-lg">
              <Notice tone="error">Không tạo được: {error}</Notice>
            </div>
          )}
          <Section n={1} title="Chỉ số">
            <Select
              label="Chỉ số"
              value={metricId}
              onChange={setMetricId}
              options={options.metrics.map((m) => ({ value: m.id, label: m.label }))}
            />
            <div className="grid grid-cols-2 gap-da-sm">
              <Field label="Phạm vi">
                <Select
                  label="Phạm vi"
                  value={scope}
                  onChange={setScope}
                  options={options.scopes}
                />
              </Field>
              <Field label="Tách theo">
                <Select
                  label="Tách theo"
                  value={split}
                  onChange={(v) => {
                    setSplit(v);
                    setGroup(options.splits.find((s) => s.value === v)?.groups[0] ?? '');
                  }}
                  options={options.splits.map((s) => ({ value: s.value, label: s.label }))}
                />
              </Field>
            </div>
          </Section>
          <Section n={2} title="Điều kiện">
            <div>
              <Segmented<RuleKind>
                variant="outline"
                label="Kiểu điều kiện"
                value={kind}
                onChange={setKind}
                items={KINDS}
              />
            </div>
            {lacking && metric ? (
              <Notice tone="warning">
                Chỉ số này mới có {metric.history_days} ngày dữ liệu. Điều kiện{' '}
                {KINDS.find((k) => k.value === kind)?.label} cần ít nhất {lacking} ngày để học
                khoảng bình thường — dùng được từ{' '}
                {`${String(ready.getDate()).padStart(2, '0')}/${String(ready.getMonth() + 1).padStart(2, '0')}`}
                . Trong lúc chờ, bạn có thể dùng Ngưỡng hoặc Thay đổi.
              </Notice>
            ) : kind === 'threshold' ? (
              <>
                <Line>
                  <span>Khi giá trị</span>
                  <Narrow>
                    <Select
                      label="Hướng"
                      value={direction}
                      onChange={setDirection}
                      options={DIRECTIONS}
                    />
                  </Narrow>
                  <NumberBox label="Ngưỡng" value={value} onChange={setValue} unit={unit} />
                  <span>trong</span>
                  <Narrow>
                    <Select label="Trong" value={window_} onChange={setWindow} options={WINDOWS} />
                  </Narrow>
                </Line>
                <Line>
                  <span className="text-muted-foreground">Cảnh báo sớm</span>
                  <NumberBox label="Cảnh báo sớm" value={early} onChange={setEarly} unit={unit} />
                  <span className="text-muted-foreground">Hồi phục dưới</span>
                  <NumberBox
                    label="Hồi phục dưới"
                    value={recover}
                    onChange={setRecover}
                    unit={unit}
                  />
                </Line>
              </>
            ) : kind === 'change_vs_previous' ? (
              <Line>
                <span>Khi giá trị</span>
                <Narrow>
                  <Select
                    label="Hướng thay đổi"
                    value={changeDirection}
                    onChange={setChangeDirection}
                    options={CHANGE_DIRECTIONS}
                  />
                </Narrow>
                <NumberBox label="Mức thay đổi" value={change} onChange={setChange} unit="%" />
                <span>so với</span>
                <Narrow>
                  <Select label="So với" value={base} onChange={setBase} options={BASES} />
                </Narrow>
              </Line>
            ) : kind === 'seasonal_anomaly' ? (
              <Line>
                <span>Báo khi lệch khỏi khoảng bình thường theo mùa vụ, độ nhạy</span>
                <Narrow>
                  <Select
                    label="Độ nhạy"
                    value={sensitivity}
                    onChange={setSensitivity}
                    options={SENSITIVITY}
                  />
                </Narrow>
              </Line>
            ) : (
              <Line>
                <span>Khi dự báo dưới</span>
                <NumberBox label="Ngưỡng dự báo" value={value} onChange={setValue} unit={unit} />
                <span>trong</span>
                <Narrow>
                  <Select
                    label="Tầm dự báo"
                    value={horizon}
                    onChange={setHorizon}
                    options={HORIZONS}
                  />
                </Narrow>
              </Line>
            )}
          </Section>
          <Section n={3} title="Gửi tới">
            <div className="grid grid-cols-2 gap-da-sm">
              <Field label="Người nhận">
                <Select
                  label="Người nhận"
                  value={who}
                  onChange={setWho}
                  options={options.recipients}
                />
              </Field>
              <Field label="Kênh">
                {metric?.certified ? (
                  <Select
                    label="Kênh"
                    value={channels}
                    onChange={setChannels}
                    options={options.channels}
                  />
                ) : (
                  <Tooltip content="Chỉ số chưa chứng nhận chỉ báo trong ứng dụng.">
                    <Select
                      label="Kênh"
                      value="in_app"
                      onChange={() => {}}
                      disabled
                      options={[{ value: 'in_app', label: 'Chỉ trong ứng dụng' }]}
                    />
                  </Tooltip>
                )}
              </Field>
            </div>
            <p className="text-muted-foreground da-caption">
              Nhắc lại sau 4 giờ nếu chưa ai mở. Tối đa 1 cảnh báo mỗi vùng mỗi ngày.
            </p>
          </Section>
          <Section n={4} title="Khi kích hoạt">
            <div className="flex items-center gap-da-md">
              <Toggle
                checked={withAnalysis}
                onChange={setWithAnalysis}
                label="Agent Phân tích tìm nguyên nhân"
              />
              <span className="text-foreground da-body-small">
                Agent Phân tích tìm nguyên nhân và đính kèm vào cảnh báo
              </span>
            </div>
            <p className="text-muted-foreground da-caption">{options.analysis_cost_text}</p>
          </Section>
        </div>
        <aside className="flex min-w-0 flex-1 flex-col gap-da-lg border-l border-border bg-background px-da-2xl pt-da-xl max-[1023px]:border-l-0 max-[1023px]:border-t">
          <div className="flex items-center gap-da-md">
            <h2 className="text-foreground da-heading-3">Xem trước trên 90 ngày</h2>
            {groups.length > 1 && (
              <div className="ml-auto w-[180px]">
                <Select
                  label="Nhóm xem trước"
                  value={group}
                  onChange={setGroup}
                  options={groups.map((g) => ({ value: g, label: g }))}
                />
              </div>
            )}
          </div>
          {preview?.series && !lacking ? (
            <>
              <div className="rounded-md border border-border bg-card p-da-lg">
                <PreviewChart preview={preview} unit={unit} />
              </div>
              <p className="text-foreground da-body">{preview.text}</p>
              {noisy && (
                <Notice tone="warning">
                  Theo dõi này báo ở hơn 30% số kỳ — dễ gây nhiễu (BR-32). Cân nhắc nâng ngưỡng.
                </Notice>
              )}
              {preview.note && (
                <p className="text-muted-foreground da-body-small">{preview.note}</p>
              )}
              <p className="text-muted-foreground da-caption">
                Chỉ số phải được chứng nhận thì cảnh báo mới gửi ra kênh ngoài.
              </p>
            </>
          ) : (
            <p className="text-muted-foreground da-body">
              {lacking || preview ? 'Chưa đủ dữ liệu để xem trước.' : 'Đang xem trước…'}
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
      <h2 className="flex items-baseline gap-da-sm text-foreground da-heading-3">
        <span className="text-muted-foreground da-caption">{n}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

function Line({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-da-sm text-foreground da-body-small">
      {children}
    </div>
  );
}

function Narrow({ children }: { children: React.ReactNode }) {
  return <div className="w-[110px]">{children}</div>;
}

function NumberBox({
  label,
  value,
  onChange,
  unit,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  unit: string;
}) {
  return (
    <span className="inline-flex h-8 w-[80px] items-center gap-da-xs rounded-sm border border-border-strong bg-card px-da-sm">
      <input
        aria-label={label}
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-mono"
      />
      <span className="text-muted-foreground da-mono">{unit}</span>
    </span>
  );
}
