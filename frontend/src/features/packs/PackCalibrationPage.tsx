import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  acceptCalibrationGap,
  activatePackInstall,
  applyCalibrationFix,
  getCalibration,
  type Calibration,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Đưa vào ngành · Đối chiếu số" (2286:17389, UC-09-02): metrics computed from the mapped
 * data against what the organisation reported for the last three periods, within a tolerance.
 * A gap comes with what the Agent Đối soát found and, when it can, a fix to the mapping; otherwise
 * the data owner accepts it with a note. Finishing the onboarding waits until every row matches or
 * is accepted (BR-36).
 */
export default function PackCalibrationPage() {
  const { routeId } = useAppState();
  const calibration = useV1(routeId ? () => getCalibration(routeId) : null, [routeId]);
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Mở đối chiếu số từ một lượt đưa vào ngành.
      </p>
    );
  return (
    <AsyncRegion state={calibration} what="đối chiếu số">
      {(c) => (
        <CalibrationBody installId={routeId} calibration={c} onChanged={calibration.reload} />
      )}
    </AsyncRegion>
  );
}

function CalibrationBody({
  installId,
  calibration,
  onChanged,
}: {
  installId: string;
  calibration: Calibration;
  onChanged: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const [accepting, setAccepting] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const done = calibration.matched === calibration.total;

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

  const finish = () =>
    act(
      'activate',
      () => activatePackInstall(installId),
      `Đã gửi duyệt kích hoạt ${calibration.install_title}`,
      () => navigate('data', 'PACKS'),
    );

  const finishButton = (
    <Button
      kind="primary"
      loading={busy === 'activate'}
      disabled={busy !== null || !done}
      onClick={finish}
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
            <StatusDot tone={done ? 'success' : 'warning'}>
              {calibration.matched}/{calibration.total} khớp
            </StatusDot>
          </span>
        }
        description={`So chỉ số tính từ dữ liệu đã ánh xạ với số tổ chức đã báo cáo 3 kỳ gần nhất · ${calibration.tolerance_text}`}
        actions={
          <>
            <Button kind="ghost" onClick={() => navigate('data', 'PACK_ONBOARD', installId)}>
              Quay lại ánh xạ
            </Button>
            {done ? (
              finishButton
            ) : (
              <Tooltip content="Còn chỉ số lệch chưa xử lý (BR-36).">{finishButton}</Tooltip>
            )}
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-3xl pb-da-2xl pt-da-xl">
          {error && !accepting && <Notice tone="error">Không thực hiện được: {error}</Notice>}
          <table className="w-full table-fixed border-collapse da-body-small">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-md py-da-sm font-normal">Chỉ số</th>
                <th className="w-[92px] px-da-md py-da-sm font-normal">Kỳ</th>
                <th className="w-[132px] px-da-md py-da-sm font-normal">Tính từ dữ liệu</th>
                <th className="w-[122px] px-da-md py-da-sm font-normal">Đã báo cáo</th>
                <th className="w-[92px] px-da-md py-da-sm font-normal">Lệch</th>
                <th className="w-[130px] px-da-md py-da-sm font-normal">Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {calibration.rows.map((r) => (
                <tr key={r.id} className="border-b border-border text-foreground">
                  <td className="px-da-md py-[9px]">{r.metric}</td>
                  <td className="px-da-md py-[9px] text-muted-foreground da-mono">{r.period}</td>
                  <td className="px-da-md py-[9px] da-mono">{r.computed}</td>
                  <td className="px-da-md py-[9px] da-mono">{r.reported}</td>
                  <td
                    className={cn(
                      'px-da-md py-[9px] da-mono',
                      !r.ok && !r.accepted_note && 'text-error',
                    )}
                  >
                    {r.diff}
                  </td>
                  <td className="px-da-md py-[9px]">
                    {r.ok ? (
                      <StatusDot tone="success">Khớp</StatusDot>
                    ) : r.accepted_note ? (
                      <Tooltip content={r.accepted_note}>
                        <span>
                          <StatusDot tone="info">Chấp nhận lệch</StatusDot>
                        </span>
                      </Tooltip>
                    ) : (
                      <StatusDot tone="error">Lệch</StatusDot>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {calibration.findings.map((f) => (
            <section
              key={f.row_id}
              aria-label={f.title}
              className="flex flex-col gap-da-sm rounded-md bg-error-subtle p-da-lg"
            >
              <h2 className="text-foreground da-heading-3">{f.title}</h2>
              <p className="text-foreground da-body-small">{f.text}</p>
              <div className="flex flex-wrap gap-da-sm">
                {f.fix && (
                  <Button
                    loading={busy === f.fix.id}
                    disabled={busy !== null}
                    onClick={() =>
                      act(
                        f.fix!.id,
                        () => applyCalibrationFix(installId, f.fix!.id),
                        'Đã sửa ánh xạ; đối chiếu lại',
                      )
                    }
                  >
                    {f.fix.label}
                  </Button>
                )}
                <Button
                  kind="ghost"
                  disabled={busy !== null}
                  onClick={() => {
                    setNote('');
                    setError(undefined);
                    setAccepting(f.row_id);
                  }}
                >
                  Ghi chú lệch được chấp nhận
                </Button>
              </div>
            </section>
          ))}
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Hoàn tất khi nào</h2>
          <p className="text-foreground da-body-small">
            Mọi chỉ số đối chiếu khớp trong ngưỡng, hoặc phần lệch được chủ sở hữu dữ liệu ghi chú
            chấp nhận.
          </p>
          <h2 className="text-foreground da-heading-3">Sau khi hoàn tất</h2>
          <p className="text-foreground da-body-small">{calibration.after_text}</p>
          <p className="text-muted-foreground da-caption">Đối chiếu số là điều kiện để hoàn tất.</p>
        </aside>
      </div>
      <ConfirmDialog
        open={accepting !== null}
        title="Chấp nhận phần lệch?"
        confirmLabel="Chấp nhận"
        cancelLabel="Quay lại"
        loading={busy === 'accept'}
        error={accepting ? error : undefined}
        onCancel={() => setAccepting(null)}
        onConfirm={() => {
          if (!note.trim()) {
            setError('Ghi lý do chấp nhận phần lệch.');
            return;
          }
          const rowId = accepting!;
          act(
            'accept',
            () => acceptCalibrationGap(installId, rowId, note.trim()),
            'Đã ghi chú phần lệch',
            () => setAccepting(null),
          );
        }}
      >
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Lý do (bắt buộc)</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
          />
        </label>
      </ConfirmDialog>
    </div>
  );
}
