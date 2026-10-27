import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  activatePackInstall,
  calibratePackInstall,
  createMetricOverride,
  getCalibration,
  type Calibration,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "P09 · Đưa vào ngành · Đối chiếu số" (2286:17389, UC-09-02): metrics computed from the mapped
 * data against what the organisation reported, within a tolerance. A gap comes with what the
 * Agent Đối soát found. It is fixed by changing the mapping and reconciling again (5a), or, when
 * the organisation computes the metric its own way, by an override sent for approval — a gap is
 * never just accepted (BR-36). Finishing sends the activation for approval; the api refuses it
 * while a regulatory metric differs without an approved override, other metrics do not block (4a).
 */
export default function PackCalibrationPage() {
  const { routeId } = useAppState();
  const calibration = useV1(['getCalibration', routeId], routeId ? () => getCalibration(routeId) : null);
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Mở đối chiếu số từ một lượt đưa vào ngành.
      </p>
    );
  return (
    <AsyncRegion state={calibration} what="đối chiếu số">
      {(c) => <CalibrationBody calibration={c} onChanged={calibration.reload} />}
    </AsyncRegion>
  );
}

function CalibrationBody({
  calibration,
  onChanged,
}: {
  calibration: Calibration;
  onChanged: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const installId = calibration.install_id;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [adjusting, setAdjusting] = useState<string | null>(null);
  const finished = calibration.run_status !== null && calibration.run_status !== 'running';
  const allMatch = finished && calibration.matched === calibration.total;

  const act = async (
    key: string,
    call: () => Promise<unknown>,
    message: string,
    after?: () => void,
  ) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      after?.();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const finishButton = (
    <Button
      kind="primary"
      loading={busy === 'activate'}
      disabled={busy !== null || !finished}
      onClick={() =>
        act(
          'activate',
          () => activatePackInstall(installId),
          `Đã gửi duyệt kích hoạt ${calibration.install_title}`,
          () => navigate('data', 'PACKS'),
        )
      }
    >
      Hoàn tất đưa vào ngành
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Gói ngành', calibration.install_title, 'Đối chiếu số']}
        title={
          <span className="flex items-center gap-[10px]">
            Đối chiếu số đã báo cáo
            {calibration.run_status === null ? (
              <StatusDot tone="neutral">Chưa đối chiếu</StatusDot>
            ) : calibration.run_status === 'running' ? (
              <StatusDot tone="info">Đang đối chiếu</StatusDot>
            ) : (
              <StatusDot tone={allMatch ? 'success' : 'warning'}>
                {calibration.matched}/{calibration.total} khớp
              </StatusDot>
            )}
          </span>
        }
        description={[
          'So chỉ số tính từ dữ liệu đã ánh xạ với số tổ chức đã báo cáo',
          calibration.periods.length ? `kỳ ${calibration.periods.join(', ')}` : '',
          calibration.tolerance_text,
        ]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <>
            <LinkButton area="data" page="PACK_ONBOARD" id={installId} kind="ghost">
              Quay lại ánh xạ
            </LinkButton>
            <Button
              loading={busy === 'calibrate'}
              disabled={busy !== null || calibration.run_status === 'running'}
              onClick={() =>
                act('calibrate', () => calibratePackInstall(installId), 'Đang đối chiếu lại')
              }
            >
              {calibration.run_status === null ? 'Đối chiếu số' : 'Đối chiếu lại'}
            </Button>
            {finished ? (
              finishButton
            ) : (
              <Tooltip content="Chờ lượt đối chiếu số xong.">{finishButton}</Tooltip>
            )}
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-3xl pb-da-2xl pt-da-xl">
          {error && !adjusting && <Notice tone="error">Không thực hiện được: {error}</Notice>}
          {calibration.rows.length === 0 ? (
            <p className="text-muted-foreground da-body-small">
              {calibration.run_status === 'running'
                ? 'Agent Đối soát đang tính các chỉ số trên dữ liệu đã ánh xạ.'
                : 'Chưa có kết quả đối chiếu. Duyệt ánh xạ rồi chạy đối chiếu số.'}
            </p>
          ) : (
            <CalibrationTable rows={calibration.rows} />
          )}
          {calibration.findings.map((f) => (
            <section
              key={f.row_id}
              aria-label={f.title}
              className="flex flex-col gap-da-sm rounded-md bg-error-subtle p-da-lg"
            >
              <h2 className="text-foreground da-heading-3">{f.title}</h2>
              <p className="text-foreground da-body-small">{f.text}</p>
              <div className="flex flex-wrap gap-da-sm">
                <LinkButton area="data" page="PACK_ONBOARD" id={installId} disabled={busy !== null}>
                  Sửa ánh xạ
                </LinkButton>
                <Button
                  kind="ghost"
                  disabled={busy !== null}
                  onClick={() => {
                    setError(undefined);
                    setAdjusting(f.metric_code);
                  }}
                >
                  Hiệu chỉnh {f.metric_code} cho tổ chức
                </Button>
              </div>
            </section>
          ))}
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Hoàn tất khi nào</h2>
          <p className="text-foreground da-body-small">
            Chỉ số lệch được sửa ánh xạ rồi đối chiếu lại, hoặc có hiệu chỉnh của tổ chức đã được
            duyệt.
          </p>
          <h2 className="text-foreground da-heading-3">Sau khi hoàn tất</h2>
          <p className="text-foreground da-body-small">{calibration.after_text}</p>
          <p className="text-muted-foreground da-caption">Đối chiếu số là điều kiện để hoàn tất.</p>
        </aside>
      </div>
      {adjusting && (
        <OverrideDialog
          installId={installId}
          metricCode={adjusting}
          onClose={() => setAdjusting(null)}
          onCreated={(id) => navigate('data', 'METRIC_OVERRIDE', id)}
        />
      )}
    </div>
  );
}

/** UC-09-03: the organisation's own formula for a pack metric, with its reason (BR-129). */
function OverrideDialog({
  installId,
  metricCode,
  onClose,
  onCreated,
}: {
  installId: string;
  metricCode: string;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const toast = useToast();
  const [expression, setExpression] = useState('');
  const [scope, setScope] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const created = await createMetricOverride({
        pack_install_id: installId,
        pack_metric_code: metricCode,
        expression: expression.trim(),
        reason: reason.trim(),
        scope_condition: scope.trim() || null,
      });
      toast({ tone: 'success', message: `Đã gửi duyệt hiệu chỉnh ${metricCode}` });
      onCreated(created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      title={`Hiệu chỉnh ${metricCode}`}
      description="Công thức của tổ chức thay cho công thức của gói; gửi duyệt ngay khi tạo."
      onClose={onClose}
      onSubmit={submit}
      submitLabel="Gửi duyệt"
      submitting={saving}
      canSubmit={Boolean(expression.trim() && reason.trim())}
      error={error}
    >
      <div className="flex flex-col gap-da-lg">
        <Field label="Công thức của tổ chức" labelTone="muted">
          <Textarea mono value={expression} onChange={(e) => setExpression(e.target.value)} />
        </Field>
        <Field label="Điều kiện phạm vi (không bắt buộc)" labelTone="muted">
          <Input value={scope} onChange={(e) => setScope(e.target.value)} />
        </Field>
        <Field label="Lý do" labelTone="muted">
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

function CalibrationTable({ rows }: { rows: Calibration['rows'] }) {
  const view = useTableView(rows, {
    search: (r) => `${r.metric} ${r.period} ${r.computed} ${r.reported} ${r.diff}`,
    filters: [{ id: 'result', label: 'Kết quả', value: (r) => (r.ok ? 'Khớp' : 'Lệch') }],
    sorts: [
      { id: 'metric', value: (r) => r.metric },
      { id: 'period', value: (r) => r.period },
      { id: 'computed', value: (r) => r.computed },
      { id: 'reported', value: (r) => r.reported },
      { id: 'diff', value: (r) => r.diff },
      { id: 'result', value: (r) => (r.ok ? 'Khớp' : 'Lệch') },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm chỉ số…" searchLabel="Tìm chỉ số đối chiếu" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="metric" className="px-da-md py-da-sm font-normal">
              Chỉ số
            </SortableTh>
            <SortableTh view={view} column="period" className="w-[92px] px-da-md py-da-sm font-normal">
              Kỳ
            </SortableTh>
            <SortableTh view={view} column="computed" className="w-[132px] px-da-md py-da-sm font-normal">
              Tính từ dữ liệu
            </SortableTh>
            <SortableTh view={view} column="reported" className="w-[122px] px-da-md py-da-sm font-normal">
              Đã báo cáo
            </SortableTh>
            <SortableTh view={view} column="diff" className="w-[92px] px-da-md py-da-sm font-normal">
              Lệch
            </SortableTh>
            <SortableTh view={view} column="result" className="w-[130px] px-da-md py-da-sm font-normal">
              Kết quả
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr key={r.id} className="border-b border-border text-foreground">
              <td className="px-da-md py-[9px]">{r.metric}</td>
              <td className="px-da-md py-[9px] text-muted-foreground da-mono">{r.period}</td>
              <td className="px-da-md py-[9px] da-mono">{r.computed}</td>
              <td className="px-da-md py-[9px] da-mono">{r.reported}</td>
              <td className={cn('px-da-md py-[9px] da-mono', !r.ok && 'text-error')}>{r.diff}</td>
              <td className="px-da-md py-[9px]">
                {r.ok ? (
                  <StatusDot tone="success">Khớp</StatusDot>
                ) : (
                  <StatusDot tone="error">Lệch</StatusDot>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
