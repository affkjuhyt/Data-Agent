import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import {
  createForecastModel,
  forecastScope,
  listCertifiedMetrics,
  type CertifiedMetricOption,
  type ForecastScopeDimension,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';

/*
 * Defining a forecast model (the series ml-forecast trains): a certified metric, optional cuts
 * on the dimensions that metric allows, month or quarter, and a horizon of 1–24 periods. The
 * create call queues the first training job; this page opens that job.
 */
const FREQUENCIES = [
  { value: 'month' as const, label: 'Tháng' },
  { value: 'quarter' as const, label: 'Quý' },
];

export default function CreateForecastModelPage() {
  const metrics = useV1(['certified-forecast-metrics'], listCertifiedMetrics);
  return (
    <AsyncRegion state={metrics} what="chỉ số đã chứng nhận">
      {(options) => <CreateForm metrics={options} />}
    </AsyncRegion>
  );
}

function CreateForm({ metrics }: { metrics: CertifiedMetricOption[] }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [metricId, setMetricId] = useState(metrics[0]?.id ?? '');
  const [frequency, setFrequency] = useState<'month' | 'quarter'>('month');
  const [horizon, setHorizon] = useState('12');
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const metric = metrics.find((item) => item.id === metricId);
  const scope = useV1(
    ['forecast-scope', metric?.id],
    metric ? () => forecastScope(metric.id) : null,
  );

  const toggle = (dimension: string, member: string, on: boolean) => {
    setPicked((current) => {
      const had = current[dimension] ?? [];
      const next = on ? [...had, member] : had.filter((code) => code !== member);
      return { ...current, [dimension]: next };
    });
  };

  const submit = async () => {
    if (sending.current) return;
    const periods = Number(horizon);
    if (!metric) {
      setError('Chọn một chỉ số đã chứng nhận.');
      return;
    }
    if (!Number.isInteger(periods) || periods < 1 || periods > 24) {
      setError('Tầm từ 1 đến 24 kỳ.');
      return;
    }
    sending.current = true;
    const cuts: Record<string, string[]> = {};
    for (const [code, members] of Object.entries(picked)) {
      if (members.length) cuts[code] = members;
    }
    setBusy(true);
    setError(undefined);
    try {
      const created = await createForecastModel({
        metric_code: metric.code,
        frequency,
        horizon: periods,
        scope: cuts,
      });
      toast({ tone: 'success', message: 'Đã tạo mô hình và xếp hàng việc huấn luyện' });
      navigate('analysis', 'FORECAST_JOB', created.job_id);
    } catch (e) {
      sending.current = false;
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <form
      className="flex min-h-full flex-col"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', 'Tạo mô hình']}
        title="Tạo mô hình dự báo"
        description="Một chỉ số đã chứng nhận, tần suất tháng hoặc quý, và tầm từ 1 đến 24 kỳ."
        actions={
          <Button kind="primary" type="submit" loading={busy} onClick={() => void submit()}>
            Tạo mô hình
          </Button>
        }
      />
      <div className="flex max-w-[640px] flex-col gap-da-xl px-da-3xl py-da-xl">
        {error && <Notice tone="error">{error}</Notice>}
        <Field label="Chỉ số">
          <Select
            label="Chỉ số"
            options={metrics.map((item) => ({ value: item.id, label: item.label }))}
            value={metric?.id ?? null}
            onChange={setMetricId}
            placeholder="Chọn chỉ số đã chứng nhận"
          />
        </Field>
        <div className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Tần suất</span>
          <Segmented<'month' | 'quarter'>
            label="Tần suất"
            items={FREQUENCIES}
            value={frequency}
            onChange={setFrequency}
          />
        </div>
        <Field label="Tầm" hint="Số kỳ dự báo, từ 1 đến 24.">
          <Input
            type="number"
            min={1}
            max={24}
            value={horizon}
            onChange={(event) => setHorizon(event.target.value)}
          />
        </Field>
        <ScopeEditor dimensions={scope.data ?? []} picked={picked} onToggle={toggle} />
      </div>
    </form>
  );
}

function ScopeEditor({
  dimensions,
  picked,
  onToggle,
}: {
  dimensions: ForecastScopeDimension[];
  picked: Record<string, string[]>;
  onToggle: (dimension: string, member: string, on: boolean) => void;
}) {
  if (dimensions.length === 0) return null;
  return (
    <div className="flex flex-col gap-da-lg">
      <p className="text-foreground da-label">Phạm vi</p>
      {dimensions.map((dimension) => (
        <fieldset key={dimension.code} className="flex flex-col gap-da-sm">
          <legend className="text-foreground da-body-small">{dimension.label}</legend>
          {dimension.members.length === 0 ? (
            <p className="text-muted-foreground da-caption">Chưa có giá trị cho chiều này.</p>
          ) : (
            dimension.members.map((member) => (
              <Checkbox
                key={member.code}
                checked={(picked[dimension.code] ?? []).includes(member.code)}
                onChange={(on) => onToggle(dimension.code, member.code, on)}
              >
                {member.label}
              </Checkbox>
            ))
          )}
        </fieldset>
      ))}
    </div>
  );
}
