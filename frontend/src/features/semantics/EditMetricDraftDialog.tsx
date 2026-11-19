import { useEffect, useState } from 'react';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { Textarea } from '../../design-system/Textarea';
import {
  updateMetricDraft,
  type MetricDraftEdit,
  type MetricVersion,
} from '../../lib/v1/metrics';
import { useV1Mutation } from '../../lib/v1/useV1';

const PERIODS: { value: MetricVersion['period_semantics']; label: string }[] = [
  { value: 'sum_over_period', label: 'Cộng trong kỳ (doanh thu, số giao dịch)' },
  { value: 'end_of_period', label: 'Số dư cuối kỳ (số dư tài khoản, tồn kho)' },
  { value: 'average_over_period', label: 'Bình quân trong kỳ' },
];

const GRAINS: { value: MetricVersion['time_grain']; label: string }[] = [
  { value: 'day', label: 'Ngày' },
  { value: 'month', label: 'Tháng' },
  { value: 'quarter', label: 'Quý' },
  { value: 'year', label: 'Năm' },
];

const DIRECTIONS: { value: MetricVersion['direction']; label: string }[] = [
  { value: 'higher_better', label: 'Cao hơn là tốt' },
  { value: 'lower_better', label: 'Thấp hơn là tốt' },
  { value: 'neutral', label: 'Không có chiều tốt' },
];

/**
 * A draft version is edited on its own row (PATCH /v1/metric-versions/{id}). A certified
 * version is never edited: that change is a new draft, which the page creates separately.
 */
export function EditMetricDraftDialog({
  version,
  onClose,
  onSaved,
}: {
  version: MetricVersion | null;
  onClose: () => void;
  onSaved: (saved: MetricVersion) => void;
}) {
  const [name, setName] = useState('');
  const [expression, setExpression] = useState('');
  const [unit, setUnit] = useState('');
  const [decimals, setDecimals] = useState('2');
  const [direction, setDirection] = useState<MetricVersion['direction']>('neutral');
  const [period, setPeriod] = useState<MetricVersion['period_semantics']>('sum_over_period');
  const [grain, setGrain] = useState<MetricVersion['time_grain']>('month');
  const [note, setNote] = useState('');
  const [invalid, setInvalid] = useState<string>();
  const save = useV1Mutation((edit: MetricDraftEdit) => updateMetricDraft(version!.id, edit), {
    invalidates: [['getMetric']],
    onSuccess: onSaved,
  });

  useEffect(() => {
    if (!version) return;
    setName(version.display_name);
    setExpression(version.expression);
    setUnit(version.unit ?? '');
    setDecimals(String(version.decimals));
    setDirection(version.direction);
    setPeriod(version.period_semantics);
    setGrain(version.time_grain);
    setNote(version.change_note ?? '');
    setInvalid(undefined);
    save.reset();
  }, [version]);

  const submit = () => {
    const places = Number(decimals);
    const problem =
      !name.trim() || !expression.trim()
        ? 'Nhập tên hiển thị và công thức.'
        : !Number.isInteger(places) || places < 0 || places > 10
          ? 'Số chữ số thập phân là số nguyên từ 0 đến 10.'
          : undefined;
    setInvalid(problem);
    if (problem || !version) {
      save.reset();
      return;
    }
    save.run({
      display_name: name.trim(),
      expression: expression.trim(),
      unit: unit.trim() || null,
      decimals: places,
      direction,
      period_semantics: period,
      time_grain: grain,
      change_note: note.trim() || null,
    });
  };

  return (
    <Dialog
      open={version !== null}
      title={version ? `Sửa bản nháp v${version.version}` : 'Sửa bản nháp'}
      description="Bản nháp sửa tại chỗ. Bản đã chứng nhận không đổi; muốn đổi thì tạo bản nháp mới."
      submitLabel="Lưu bản nháp"
      submitting={save.pending}
      error={invalid ?? save.error?.message}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Tên hiển thị">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="w-full" />
      </Field>
      <Field label="Công thức">
        <Textarea
          mono
          rows={4}
          value={expression}
          onChange={(e) => setExpression(e.target.value)}
          className="w-full"
        />
      </Field>
      <Field label="Đơn vị">
        <Input value={unit} onChange={(e) => setUnit(e.target.value)} className="w-full" />
      </Field>
      <Field label="Số chữ số thập phân">
        <Input
          value={decimals}
          inputMode="numeric"
          onChange={(e) => setDecimals(e.target.value)}
          className="w-full"
        />
      </Field>
      <Field label="Chiều tốt">
        <Select<MetricVersion['direction']>
          label="Chiều tốt"
          options={DIRECTIONS}
          value={direction}
          onChange={setDirection}
        />
      </Field>
      <Field label="Cách đọc theo kỳ">
        <Select<MetricVersion['period_semantics']>
          label="Cách đọc theo kỳ"
          options={PERIODS}
          value={period}
          onChange={setPeriod}
        />
      </Field>
      <Field label="Hạt thời gian">
        <Select<MetricVersion['time_grain']>
          label="Hạt thời gian"
          options={GRAINS}
          value={grain}
          onChange={setGrain}
        />
      </Field>
      <Field label="Ghi chú thay đổi">
        <Input value={note} onChange={(e) => setNote(e.target.value)} className="w-full" />
      </Field>
    </Dialog>
  );
}
