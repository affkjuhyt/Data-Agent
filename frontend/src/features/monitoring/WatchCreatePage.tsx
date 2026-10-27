import { useState } from 'react';
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
  getMetricShape,
  getMonitorOptions,
  type MonitorDraft,
  type MonitorOptions,
  type RuleKind,
} from '../../lib/v1/monitors';
import { useV1 } from '../../lib/v1/useV1';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "P08 · Theo dõi · Tạo theo dõi" (2270:16233, UC-08-01): metric and how to split it, the rule, who
 * hears of it, and whether the Agent Phân tích attaches a cause. A2 creates the monitor active and
 * the monitor agent then backtests it on the last 12 periods; above 30% of periods is noisy (BR-32)
 * and the list marks it. A2 has no preview of an unsaved rule, so the frame's 90-day preview and
 * the forecast rule kind (not in monitor.rule_kind) are not drawn. An uncertified metric alerts
 * inside the app only.
 */
const KINDS: { value: RuleKind; label: string }[] = [
  { value: 'threshold', label: 'Ngưỡng' },
  { value: 'change_vs_previous', label: 'Thay đổi' },
  { value: 'seasonal_anomaly', label: 'Bất thường' },
];
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

const parse = (s: string) => Number(s.replace(',', '.'));
const valid = (s: string) => s.trim() !== '' && Number.isFinite(parse(s));

export default function WatchCreatePage() {
  const options = useV1(['getMonitorOptions'], getMonitorOptions);
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
  const shape = useV1(['getMetricShape', metricId], metricId ? () => getMetricShape(metricId) : null);
  const [split, setSplit] = useState('none');
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
  const [who, setWho] = useState(options.recipients[0]?.value ?? '');
  const [channels, setChannels] = useState(options.channels[0]?.value ?? '');
  const [withAnalysis, setWithAnalysis] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const metric = options.metrics.find((m) => m.id === metricId);
  const unit = shape.data?.unit ?? '';
  const splits = shape.data?.splits ?? [{ value: 'none', label: 'Không tách' }];

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
        : {
            sensitivity,
            text: `bất thường · độ nhạy ${SENSITIVITY.find((s) => s.value === sensitivity)?.label}`,
          };
  const ruleOk =
    kind === 'threshold' ? valid(value) : kind === 'change_vs_previous' ? valid(change) : true;
  const draft: MonitorDraft = {
    metric_id: metricId,
    split,
    rule_kind: kind,
    rule,
    schedule_cron: DAILY,
    notify_mode: withAnalysis ? 'with_analysis' : 'alert_only',
    recipients: { who: who ? [who] : [], channels: metric?.certified ? channels : 'in_app' },
  };

  const blocker = !metric
    ? 'Chọn chỉ số.'
    : !ruleOk
      ? 'Nhập giá trị cho điều kiện.'
      : null;

  const create = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await createMonitor(draft);
      toast({ tone: 'success', message: `Đã tạo theo dõi ${metric?.name ?? ''}` });
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
            <LinkButton area="analysis" page="WATCH" kind="ghost">
              Huỷ
            </LinkButton>
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
              onChange={(v) => {
                setMetricId(v);
                setSplit('none');
              }}
              options={options.metrics.map((m) => ({ value: m.id, label: m.label }))}
            />
            <Field label="Tách theo">
              <Select label="Tách theo" value={split} onChange={setSplit} options={splits} />
            </Field>
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
            {kind === 'threshold' ? (
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
            ) : (
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
          </Section>
        </div>
        <aside className="flex min-w-0 flex-1 flex-col gap-da-lg border-l border-border bg-background px-da-2xl pt-da-xl max-[1023px]:border-l-0 max-[1023px]:border-t">
          <h2 className="text-foreground da-heading-3">Sau khi tạo</h2>
          <p className="text-foreground da-body">
            Agent theo dõi chạy thử điều kiện trên 12 kỳ gần nhất. Nếu điều kiện báo ở hơn 30% số
            kỳ, danh sách Theo dõi đánh dấu nó dễ gây nhiễu (BR-32) để bạn nâng ngưỡng.
          </p>
          <p className="text-muted-foreground da-caption">
            Chỉ số phải được chứng nhận thì cảnh báo mới gửi ra kênh ngoài.
          </p>
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
