import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Menu } from '../../design-system/Menu';
import { Icon } from '../../design-system/Icon';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import {
  addModel,
  getAiPolicy,
  getGpuLoad,
  listModelRoutes,
  rollbackModel,
  runModelEvals,
  type GpuLoad,
  type ModelRoute,
} from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';
import { formatHour, formatLatency, pct } from './format';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P11 · Mô hình & chính sách AI" (2306:19746, UC-11-05 and UC-11-04): which model serves each
 * task (with its fallback, p95 and eval score), the org-wide AI guardrails, and the 24 h GPU load
 * on the right. Models run on the bank's own servers; changing one always reruns its eval set.
 *
 * Changes apply through their own dialogs: "Thêm mô hình" (2095:8757) registers and evaluates,
 * "Quay lại …" (2094:8469) rolls a task back. The frame's page-level "Lưu" has nothing left to
 * save, so "Thêm mô hình" takes its place as the primary action.
 */
const RATE_LIMITS = [
  { value: '30', label: '30 yêu cầu/phút' },
  { value: '60', label: '60 yêu cầu/phút' },
  { value: '120', label: '120 yêu cầu/phút' },
];

export default function ModelsPage() {
  const toast = useToast();
  const routes = useV1(listModelRoutes, []);
  const policy = useV1(getAiPolicy, []);
  const gpu = useV1(getGpuLoad, []);
  const [adding, setAdding] = useState(false);
  const [rollback, setRollback] = useState<ModelRoute | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const evaluate = async () => {
    setBusy(true);
    try {
      await runModelEvals();
      toast({ tone: 'success', message: 'Đã bắt đầu chạy bộ đánh giá cho mọi tác vụ.' });
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const confirmRollback = async () => {
    if (!rollback) return;
    setBusy(true);
    setError(undefined);
    try {
      await rollbackModel(rollback.id);
      toast({
        tone: 'success',
        message: `“${rollback.task}” đã quay lại ${rollback.previous?.model_name}.`,
      });
      setRollback(null);
      routes.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Mô hình AI']}
        title="Mô hình và chính sách AI"
        description="Mô hình chạy trên máy chủ của ngân hàng · không gửi dữ liệu ra ngoài · đổi mô hình cần chạy bộ đánh giá"
        actions={
          <>
            <Button onClick={evaluate} disabled={busy}>
              Chạy bộ đánh giá
            </Button>
            <Button kind="primary" onClick={() => setAdding(true)}>
              Thêm mô hình
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl pb-da-3xl">
          <h2 className="text-foreground da-heading-3">Mô hình theo tác vụ</h2>
          <AsyncRegion
            state={routes}
            what="mô hình"
            skeletonRows={5}
            isEmpty={(p) => p.items.length === 0}
            empty={<p className="text-muted-foreground da-body-small">Chưa có tác vụ nào.</p>}
          >
            {(p) => (
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="px-da-lg py-da-md font-normal">Tác vụ</th>
                    <th className="w-[212px] py-da-md font-normal">Mô hình</th>
                    <th className="w-[192px] py-da-md font-normal">Dự phòng</th>
                    <th className="w-[112px] py-da-md font-normal">Độ trễ p95</th>
                    <th className="w-[110px] py-da-md font-normal">Bộ đánh giá</th>
                    <th className="w-[40px]">
                      <span className="sr-only">Thao tác</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {p.items.map((route) => (
                    <tr key={route.id} className="border-b border-border">
                      <td className="px-da-lg py-da-md text-foreground da-body-strong">
                        {route.task}
                      </td>
                      <td className="py-da-md text-foreground da-mono">
                        {route.model_name}
                        {route.placement && ` · ${route.placement}`}
                        {route.status === 'evaluating' && (
                          <span className="text-muted-foreground"> · đang đánh giá</span>
                        )}
                      </td>
                      <td className="py-da-md text-muted-foreground da-mono">
                        {route.fallback_name ?? '—'}
                      </td>
                      <td className="py-da-md text-foreground da-mono">
                        {route.latency_p95_ms ? formatLatency(route.latency_p95_ms, true) : '—'}
                      </td>
                      <td className="py-da-md text-foreground da-mono">
                        {pct(route.eval_score)}
                        {(route.latest_eval_delta ?? 0) < 0 && (
                          <span className="text-error">
                            {' '}
                            ↓<span className="sr-only">giảm</span>
                          </span>
                        )}
                      </td>
                      <td className="py-da-xs text-right">
                        {route.previous && (
                          <Menu
                            label={`Thao tác với ${route.task}`}
                            items={[
                              {
                                label: `Quay lại ${route.previous.model_name}`,
                                icon: 'rollback',
                                onSelect: () => {
                                  setError(undefined);
                                  setRollback(route);
                                },
                              },
                            ]}
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </AsyncRegion>

          <h2 className="text-foreground da-heading-3">Chính sách AI</h2>
          <AsyncRegion state={policy} what="chính sách AI" skeletonRows={4}>
            {(data) => (
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="px-da-lg py-da-md font-normal">Quy định</th>
                    <th className="w-[272px] py-da-md pr-da-lg font-normal">Giá trị</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rules.map((rule) => (
                    <tr
                      key={rule.key}
                      className="border-b border-border text-foreground da-body-small"
                    >
                      <td className="px-da-lg py-da-md">{rule.label}</td>
                      <td className="py-da-md pr-da-lg">{rule.value_text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </AsyncRegion>
        </div>

        <aside
          aria-labelledby="gpu-title"
          className="flex w-[320px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t"
        >
          <h2 id="gpu-title" className="text-foreground da-heading-3">
            Tải GPU 24 giờ
          </h2>
          <AsyncRegion state={gpu} what="tải GPU" skeletonRows={2}>
            {(data) => <GpuPanel data={data} />}
          </AsyncRegion>
        </aside>
      </div>

      {adding && (
        <AddModelDialog
          onClose={() => setAdding(false)}
          onAdded={(route) => {
            setAdding(false);
            toast({
              tone: 'success',
              message: `Đã thêm ${route.model_name}; bộ đánh giá đang chạy trước khi định tuyến.`,
            });
            routes.reload();
          }}
        />
      )}

      <ConfirmDialog
        open={rollback !== null}
        tone="primary"
        title={`Quay lại ${rollback?.previous?.model_name ?? ''}?`}
        confirmLabel="Quay lại"
        loading={busy}
        error={error}
        onConfirm={confirmRollback}
        onCancel={() => setRollback(null)}
      >
        {rollback?.previous && <RollbackConsequence route={rollback} />}
      </ConfirmDialog>
    </div>
  );
}

function RollbackConsequence({ route }: { route: ModelRoute }) {
  const ratio = route.previous?.cost_ratio ?? 1;
  const cost =
    ratio === 1
      ? `Chi phí ${route.task.toLowerCase()} không đổi.`
      : `Chi phí ${route.task.toLowerCase()} ${ratio > 1 ? 'tăng' : 'giảm'} khoảng ${formatNumber(
          ratio > 1 ? ratio : 1 / ratio,
          { maxFractionDigits: 1 },
        )} lần.`;
  return (
    <>
      {cost}
      {route.eval_suite_name && ` Bộ đánh giá của ${route.eval_suite_name} sẽ chạy lại.`}
    </>
  );
}

/* The 24 h load as a line (the frame draws it 272 × 90 in primary), then the peak and the rule. */
function GpuPanel({ data }: { data: GpuLoad }) {
  const W = 272;
  const H = 90;
  const n = data.points.length;
  const points = data.points
    .map((p, i) => {
      const x = n > 1 ? (i / (n - 1)) * W : 0;
      const y = H - p.utilization * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <>
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Tải GPU theo giờ, cao nhất ${pct(data.peak.utilization, 0)} lúc ${formatHour(data.peak.at)}`}
        className="overflow-visible text-primary"
      >
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth={1.5} />
      </svg>
      <p className="text-foreground da-body-small">
        Cao điểm {pct(data.peak.utilization, 0)} lúc {formatHour(data.peak.at)}.{' '}
        {data.failover_text}
      </p>
    </>
  );
}

function AddModelDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (route: ModelRoute) => void;
}) {
  const [name, setName] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [tasks, setTasks] = useState('');
  const [limit, setLimit] = useState('60');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const route = await addModel({
        name: name.trim(),
        endpoint: endpoint.trim(),
        trial_tasks: tasks.trim(),
        rate_limit_per_min: Number(limit),
      });
      onAdded(route);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      size="sm"
      open
      title="Thêm mô hình"
      description="Mô hình chạy nội bộ; sẽ chạy bộ đánh giá trước khi được định tuyến."
      submitLabel="Thêm & đánh giá"
      submitting={saving}
      canSubmit={Boolean(name.trim() && endpoint.trim())}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Tên">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="LLM-M 24B (2026.10)"
        />
      </Field>
      <Field label="Địa chỉ">
        <Input
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          placeholder="http://llm-gw.noibo/m24"
        />
      </Field>
      <Field label="Tác vụ thử">
        <Input
          value={tasks}
          onChange={(e) => setTasks(e.target.value)}
          placeholder="Soạn báo cáo, tóm tắt"
        />
      </Field>
      <Field label="Giới hạn">
        <Select label="Giới hạn" options={RATE_LIMITS} value={limit} onChange={setLimit} />
      </Field>
      <p className="flex items-start gap-da-md rounded-md bg-info-subtle px-da-md py-da-sm text-foreground da-caption">
        <Icon name="agent" className="shrink-0 text-info" />
        Agent sẽ chạy bộ đánh giá của các tác vụ thử để so sánh với mô hình đang dùng.
      </p>
    </Dialog>
  );
}
