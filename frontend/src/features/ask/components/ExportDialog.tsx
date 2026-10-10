/**
 * "Xuất kết quả" (P01 · Hỏi đáp · Xuất kết quả, UC-01-06, 2296:19061): pick a format, then Xuất.
 *
 * Only the formats the backend can produce today are offered: the full result as Excel with its
 * "Nguồn" sheet (GET /api/ask/results/{id}/export.xlsx) and the previewed rows as CSV. The frame's
 * "Ảnh biểu đồ (PNG)", "PDF câu trả lời" and the permission-gated detail export have no backend
 * yet, so they are not drawn.
 */
import { useEffect, useState } from 'react';
import { Button } from '../../../design-system/Button';
import { Radio } from '../../../design-system/Radio';
import { formatNumber } from '../../../lib/formatVN';
import { cn } from '../../../lib/utils';
import type { EvidenceRun } from '../types';
import { AskDialog } from './AskDialog';

type Format = 'xlsx' | 'csv';

export function ExportDialog({
  open,
  run,
  onClose,
  onExportCsv,
  onExportXlsx,
}: {
  open: boolean;
  run: EvidenceRun | null;
  onClose: () => void;
  onExportCsv?: () => void;
  onExportXlsx?: () => Promise<unknown>;
}) {
  const options: Array<{ value: Format; title: string; detail: string }> = [];
  if (run && onExportXlsx) {
    options.push({
      value: 'xlsx',
      title: 'Excel · toàn bộ kết quả',
      detail: `${formatNumber(run.export?.total_rows ?? run.row_count ?? run.rows.length)} dòng, kèm trang Nguồn ghi cách tính và thời điểm dữ liệu`,
    });
  }
  if (run && onExportCsv && run.rows.length) {
    options.push({
      value: 'csv',
      title: 'CSV · bảng xem trước',
      detail: `${formatNumber(run.rows.length)} dòng đang hiện, giá trị gốc chưa định dạng, mở được bằng Excel`,
    });
  }

  const [format, setFormat] = useState<Format | null>(options[0]?.value ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setFormat(options[0]?.value ?? null);
      setError(null);
    }
    // Reset only when the dialog opens; options are derived from the same run.
  }, [open]);

  const submit = async () => {
    if (!format || busy) return;
    if (format === 'csv') {
      onExportCsv?.();
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onExportXlsx?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? `Không xuất được: ${e.message}` : 'Không xuất được tệp Excel.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <AskDialog
      open={open}
      title="Xuất kết quả"
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>Huỷ</Button>
          <Button kind="primary" onClick={submit} loading={busy} disabled={!format}>Xuất</Button>
        </>
      }
    >
      {options.length ? (
        <fieldset className="m-0 flex flex-col overflow-hidden rounded-md border border-border p-0">
          <legend className="sr-only">Định dạng</legend>
          {options.map(option => (
            <div
              key={option.value}
              className={cn(
                'border-b border-border px-[14px] py-[10px] last:border-b-0',
                format === option.value && 'bg-subtle',
              )}
            >
              <Radio name="ask-export-format" value={option.value} checked={format === option.value} onChange={v => setFormat(v as Format)}>
                <span className="flex flex-col gap-[2px]">
                  <span className="text-foreground da-body-strong">{option.title}</span>
                  <span className="text-muted-foreground da-caption">{option.detail}</span>
                </span>
              </Radio>
            </div>
          ))}
        </fieldset>
      ) : (
        <p className="m-0 text-muted-foreground da-body-small">Câu trả lời này không có bảng dữ liệu để xuất.</p>
      )}
      {error && <p role="alert" className="m-0 text-error da-caption">{error}</p>}
    </AskDialog>
  );
}
