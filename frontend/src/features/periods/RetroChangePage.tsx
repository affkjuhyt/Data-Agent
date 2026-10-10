import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { decideRetroChange, getRetroChange, type RetroChange } from '../../lib/v1/periods';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P10 · Thay đổi hồi tố" (2304:19466, UC-10-04): data of a locked period changed at the source.
 * What changed (tables, rows, fields, when), what it does to each locked figure — published ones
 * in warning — and on the right what a new version means and who else must decide. Either
 * decision needs a reason (A3: decision_note is required); a new version leaves v1 as published.
 */
const STATUS: Record<
  RetroChange['status'],
  { label: string; tone: 'warning' | 'success' | 'idle' }
> = {
  open: { label: 'Cần bạn', tone: 'warning' },
  new_version: { label: 'Đã tạo bản điều chỉnh', tone: 'success' },
  no_change: { label: 'Không điều chỉnh', tone: 'idle' },
};

type Decision = 'new_version' | 'no_change';

export default function RetroChangePage() {
  const { routeId } = useAppState();
  const change = useV1(routeId ? () => getRetroChange(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={change} what="thay đổi hồi tố">
      {(c) => <RetroBody change={c} onChanged={change.reload} />}
    </AsyncRegion>
  );
}

function RetroBody({ change, onChanged }: { change: RetroChange; onChanged: () => void }) {
  const toast = useToast();
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const status = STATUS[change.status];
  const open = change.status === 'open';

  const decide = async () => {
    if (!deciding) return;
    if (!note.trim()) {
      setError('Nhập lý do trước khi ghi nhận.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await decideRetroChange(change.id, { decision: deciding, decision_note: note.trim() });
      toast({
        tone: 'success',
        message:
          deciding === 'new_version'
            ? `Đã tạo bản điều chỉnh ${change.next_version_label}`
            : 'Đã ghi nhận: không điều chỉnh',
      });
      setDeciding(null);
      onChanged();
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
        breadcrumb={['Dữ liệu', 'Kỳ chốt', 'Thay đổi hồi tố', change.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {change.title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={change.detected_text}
        actions={
          open && (
            <>
              <Button kind="ghost" onClick={() => setDeciding('no_change')}>
                Không điều chỉnh, ghi chú
              </Button>
              <Button kind="primary" onClick={() => setDeciding('new_version')}>
                Tạo bản điều chỉnh {change.next_version_label}
              </Button>
            </>
          )
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl">
          {change.decision_note && (
            <Notice tone="info">Lý do quyết định: {change.decision_note}</Notice>
          )}
          <h2 className="text-foreground da-heading-3">Thay đổi trong nguồn</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="w-[200px] px-da-lg py-da-md font-normal">Bảng</th>
                <th className="w-[100px] px-da-lg py-da-md font-normal">Dòng đổi</th>
                <th className="px-da-lg py-da-md font-normal">Trường</th>
                <th className="w-[180px] px-da-lg py-da-md font-normal">Thời điểm sửa ở nguồn</th>
              </tr>
            </thead>
            <tbody>
              {change.source_changes.map((row) => (
                <tr key={row.table} className="border-b border-border">
                  <td className="px-da-lg py-da-md text-foreground da-mono">{row.table}</td>
                  <td className="px-da-lg py-da-md text-foreground da-mono">{row.rows}</td>
                  <td className="px-da-lg py-da-md text-muted-foreground da-mono">{row.fields}</td>
                  <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                    {row.changed_at_text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2 className="text-foreground da-heading-3">Ảnh hưởng tới số đã khoá</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Chỉ số</th>
                <th className="w-[110px] px-da-lg py-da-md font-normal">Đã khoá</th>
                <th className="w-[110px] px-da-lg py-da-md font-normal">Nếu tính lại</th>
                <th className="w-[110px] px-da-lg py-da-md font-normal">Lệch</th>
                <th className="w-[140px] px-da-lg py-da-md font-normal">Đã công bố?</th>
              </tr>
            </thead>
            <tbody>
              {change.impact.map((row) => (
                <tr key={row.metric} className="border-b border-border">
                  <td className="px-da-lg py-da-md text-foreground da-body-strong">{row.metric}</td>
                  <td className="px-da-lg py-da-md text-foreground da-mono">{row.locked}</td>
                  <td className="px-da-lg py-da-md text-foreground da-mono">{row.recalculated}</td>
                  <td className="px-da-lg py-da-md text-warning da-mono">{row.delta}</td>
                  <td
                    className={cn(
                      'px-da-lg py-da-md da-body-small',
                      row.published_to ? 'text-warning' : 'text-muted-foreground',
                    )}
                  >
                    {row.published_to ? `Có · ${row.published_to}` : 'Không'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Nếu tạo bản điều chỉnh</h2>
          <p className="text-foreground da-body-small">{change.version_note}</p>
          {change.escalation && (
            <>
              <h2 className="text-foreground da-heading-3">{change.escalation.title}</h2>
              <p className="text-warning da-body-small">{change.escalation.text}</p>
            </>
          )}
        </aside>
      </div>
      <ConfirmDialog
        open={deciding !== null}
        title={
          deciding === 'new_version'
            ? `Tạo bản điều chỉnh ${change.next_version_label}?`
            : 'Không điều chỉnh kỳ này?'
        }
        confirmLabel={deciding === 'new_version' ? 'Tạo bản điều chỉnh' : 'Ghi nhận'}
        cancelLabel="Quay lại"
        tone="primary"
        loading={busy}
        error={error}
        onCancel={() => {
          setDeciding(null);
          setError(undefined);
        }}
        onConfirm={decide}
      >
        <div className="flex flex-col gap-da-sm">
          <p>
            {deciding === 'new_version'
              ? `${change.period_code} giữ bản đã công bố; ${change.next_version_label} chứa số tính lại.`
              : 'Số đã khoá giữ nguyên; lý do được lưu cùng thay đổi này.'}
          </p>
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Lý do (bắt buộc)</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
            />
          </label>
        </div>
      </ConfirmDialog>
    </div>
  );
}
