import { useEffect, useState } from 'react';
import { Dialog } from '../../design-system/Dialog';
import { Select } from '../../design-system/Select';
import { createMart } from '../../lib/v1/dataProducts';
import { listDatasets } from '../../lib/v1/api';
import type { Dataset } from '../../lib/v1/types';

/*
 * Bootstrap a mart from a fact table already in the catalog: POST /v1/marts with empty
 * dimensions so the api fills them from active context.relationship rows (no seed).
 */
export function CreateMartDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [facts, setFacts] = useState<Dataset[]>([]);
  const [factId, setFactId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(undefined);
    setSending(false);
    setLoading(true);
    void listDatasets()
      .then((page) => {
        const rows = page.items.filter(
          (d) =>
            d.table_name.toLowerCase().startsWith('fact_') ||
            d.display_name.toLowerCase().includes('fact'),
        );
        setFacts(rows.length ? rows : page.items);
        setFactId((rows[0] ?? page.items[0])?.id ?? null);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [open]);

  const fact = facts.find((d) => d.id === factId);

  const submit = async () => {
    if (!fact) {
      setError('Chọn bảng sự kiện.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      const name = `Mart ${fact.display_name || fact.table_name}`;
      const created = await createMart({
        name,
        fact_dataset_id: fact.id,
        grain: 'theo khoá của bảng sự kiện',
        measures: [{ name: 'sales_amount', agg: 'sum' }],
      });
      onCreated(created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Tạo data mart từ bảng sự kiện"
      description="Chiều và quan hệ lấy từ các liên kết đã duyệt của bảng trong catalog — không dùng seed."
      submitLabel="Tạo mart"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Select
        label="Bảng sự kiện"
        value={factId}
        disabled={loading || facts.length === 0}
        onChange={setFactId}
        options={facts.map((d) => ({
          value: d.id,
          label: d.display_name || `${d.schema_name}.${d.table_name}`,
        }))}
        placeholder={loading ? 'Đang tải…' : 'Chọn bảng'}
      />
    </Dialog>
  );
}
