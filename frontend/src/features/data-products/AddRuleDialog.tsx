import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Radio } from '../../design-system/Radio';
import { createQualityRule, type RuleSeverity } from '../../lib/v1/dataProducts';
import { FormDialog } from '../admin-access/FormDialog';

/*
 * "Thêm quy tắc" of "P06 · Quy tắc chất lượng". The design has no dialog for it, so the fields are
 * the table's columns that a person sets (rule, where it applies, level); the owner is the caller.
 */
export function AddRuleDialog({
  open,
  onClose,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  onAdded: (name: string) => void;
}) {
  const [name, setName] = useState('');
  const [target, setTarget] = useState('');
  const [severity, setSeverity] = useState<RuleSeverity>('block');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setName('');
    setTarget('');
    setSeverity('block');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!name.trim() || !target.trim()) {
      setError('Nhập quy tắc và nơi áp vào.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      const created = await createQualityRule({ name, target, severity });
      onAdded(created.name);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <FormDialog
      open={open}
      title="Thêm quy tắc"
      description="Quy tắc chạy sau mỗi lần nạp và mỗi lần pipeline chạy."
      submitLabel="Thêm quy tắc"
      loading={sending}
      error={error}
      onSubmit={submit}
      onCancel={onClose}
    >
      <Field label="Quy tắc">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Dư nợ không âm"
        />
      </Field>
      <Field label="Áp vào">
        <Input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="prep.loans_retail.outstanding_amt"
        />
      </Field>
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
    </FormDialog>
  );
}
