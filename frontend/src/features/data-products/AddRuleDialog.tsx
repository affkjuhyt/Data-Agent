import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import { listDatasets } from '../../lib/v1/api';
import {
  createQualityRule,
  RULE_KIND,
  type RuleKind,
  type RuleSeverity,
} from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Thêm quy tắc" of "P06 · Quy tắc chất lượng". The design has no dialog for it, so the fields are
 * what A3's QualityRuleCreate takes: the table from the catalog, the column, the check and its
 * level. Only the checks whose parameters the dialog asks for are offered; the others (a list, a
 * sum, freshness, SQL) need their own parameters and are added by the data-quality agent.
 */
const KINDS: RuleKind[] = ['not_null', 'unique', 'range'];

export function AddRuleDialog({
  open,
  onClose,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: (name: string) => void;
}) {
  const datasets = useV1(open ? listDatasets : null, [open]);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [column, setColumn] = useState('');
  const [kind, setKind] = useState<RuleKind>('not_null');
  const [min, setMin] = useState('');
  const [max, setMax] = useState('');
  const [severity, setSeverity] = useState<RuleSeverity>('block');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setDatasetId(null);
    setColumn('');
    setKind('not_null');
    setMin('');
    setMax('');
    setSeverity('block');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    const table = datasets.data?.items.find((d) => d.id === datasetId);
    if (!table || !column.trim()) {
      setError('Chọn bảng và nhập cột.');
      return;
    }
    const bounds = { min: Number(min.replace(',', '.')), max: Number(max.replace(',', '.')) };
    if (
      kind === 'range' &&
      (!min.trim() || !max.trim() || isNaN(bounds.min) || isNaN(bounds.max))
    ) {
      setError('Nhập giá trị nhỏ nhất và lớn nhất.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      await createQualityRule({
        dataset_id: table.id,
        kind,
        column_name: column.trim(),
        ...(kind === 'range' ? { params: bounds } : {}),
        severity,
      });
      onAdded(`${RULE_KIND[kind]} · ${table.display_name}.${column.trim()}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Thêm quy tắc"
      description="Quy tắc chạy sau mỗi lần nạp và mỗi lần pipeline chạy."
      submitLabel="Thêm quy tắc"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Bảng">
        <Select
          label="Bảng"
          value={datasetId}
          placeholder="Chọn bảng"
          onChange={setDatasetId}
          options={(datasets.data?.items ?? []).map((d) => ({
            value: d.id,
            label: d.display_name,
          }))}
        />
      </Field>
      <Field label="Cột">
        <Input
          value={column}
          onChange={(e) => setColumn(e.target.value)}
          placeholder="outstanding_amt"
        />
      </Field>
      <Field label="Kiểm tra">
        <Select<RuleKind>
          label="Kiểm tra"
          value={kind}
          onChange={setKind}
          options={KINDS.map((k) => ({ value: k, label: RULE_KIND[k] }))}
        />
      </Field>
      {kind === 'range' && (
        <div className="flex gap-da-md">
          <Field label="Nhỏ nhất">
            <Input value={min} onChange={(e) => setMin(e.target.value)} placeholder="0" />
          </Field>
          <Field label="Lớn nhất">
            <Input value={max} onChange={(e) => setMax(e.target.value)} placeholder="100" />
          </Field>
        </div>
      )}
      <fieldset className="flex flex-col gap-da-xs">
        <legend className="text-foreground da-label">Mức</legend>
        <div className="flex gap-da-xl pt-da-xs">
          <Radio
            name="severity"
            value="block"
            checked={severity === 'block'}
            onChange={() => setSeverity('block')}
          >
            Chặn
          </Radio>
          <Radio
            name="severity"
            value="warn"
            checked={severity === 'warn'}
            onChange={() => setSeverity('warn')}
          >
            Cảnh báo
          </Radio>
        </div>
      </fieldset>
    </Dialog>
  );
}
