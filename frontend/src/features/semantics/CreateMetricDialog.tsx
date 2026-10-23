import { useEffect, useState } from 'react';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { api, ok, type Page } from '../../lib/v1/client';
import { createMetric, type NewMetric } from '../../lib/v1/metrics';
import { useV1 } from '../../lib/v1/useV1';

const PERIODS: { value: NewMetric['period_semantics']; label: string }[] = [
  { value: 'sum_over_period', label: 'Cộng trong kỳ (doanh thu, số giao dịch)' },
  { value: 'end_of_period', label: 'Số dư cuối kỳ (dư nợ, tồn kho)' },
  { value: 'average_over_period', label: 'Bình quân trong kỳ' },
];
const CODE = /^[A-Za-z][A-Za-z0-9_]{1,63}$/;

const listTables = () =>
  ok<Page<{ id: string; display_name: string | null; table_name: string }>>(
    api.GET('/v1/datasets', { params: { query: { page_size: 500 } } }),
  );

/**
 * R1-07 "Steward tạo metric qua UI" (UC-07-01 step 1): code, name, expression over the table's
 * measures, unit and how the period reads. The metric starts with a draft version: certifying it
 * goes through :preview and the metric_change proposal on its own page, as any later version does.
 */
export function CreateMetricDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (metricId: string) => void;
}) {
  const tables = useV1(['listTables', open], open ? listTables : null);
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [expression, setExpression] = useState('');
  const [unit, setUnit] = useState('');
  const [period, setPeriod] = useState<NewMetric['period_semantics']>('sum_over_period');
  const [table, setTable] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setCode('');
    setName('');
    setExpression('');
    setUnit('');
    setPeriod('sum_over_period');
    setTable(null);
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!CODE.test(code.trim())) {
      setError('Mã chỉ số bắt đầu bằng chữ, chỉ gồm chữ, số và dấu gạch dưới (2–64 ký tự).');
      return;
    }
    if (!name.trim() || !expression.trim()) {
      setError('Nhập tên hiển thị và công thức.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      onCreated(
        await createMetric({
          code: code.trim().toUpperCase(),
          display_name: name.trim(),
          expression: expression.trim(),
          unit: unit.trim() || null,
          period_semantics: period,
          canonical_dataset_id: table,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Tạo chỉ số"
      description="Chỉ số mới bắt đầu bằng một bản nháp; chạy thử và gửi duyệt ở trang của chỉ số."
      submitLabel="Tạo bản nháp"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Mã chỉ số">
        <Input value={code} placeholder="NET_REVENUE" onChange={(e) => setCode(e.target.value)} className="w-full" />
      </Field>
      <Field label="Tên hiển thị">
        <Input value={name} placeholder="Doanh thu thuần" onChange={(e) => setName(e.target.value)} className="w-full" />
      </Field>
      <Field label="Công thức">
        <Input
          value={expression}
          placeholder="doanh_thu - chiet_khau"
          onChange={(e) => setExpression(e.target.value)}
          className="w-full"
        />
      </Field>
      <Field label="Đơn vị">
        <Input value={unit} placeholder="VND" onChange={(e) => setUnit(e.target.value)} className="w-full" />
      </Field>
      <Field label="Cách đọc theo kỳ">
        <Select label="Cách đọc theo kỳ" options={PERIODS} value={period} onChange={(v) => v && setPeriod(v as NewMetric['period_semantics'])} />
      </Field>
      <Field label="Bảng chính">
        <Select
          label="Bảng chính"
          options={(tables.data?.items ?? []).map((t) => ({ value: t.id, label: t.display_name ?? t.table_name }))}
          value={table}
          onChange={setTable}
        />
      </Field>
    </Dialog>
  );
}
