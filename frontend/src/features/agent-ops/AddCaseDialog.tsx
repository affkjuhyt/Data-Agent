import { useEffect, useState } from 'react';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { addEvalCase, type NewEvalCase } from '../../lib/v1/agentOps';
import { listRuns } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Hộp thoại · addTest" (2095:9006): add a case to the eval set, optionally frozen from a real
 * run. Dialog md is the 560px of the Figma frame. The kinds are eval_item.category; cost and time
 * are deployment gates (BR-148), not eval cases.
 */
type Kind = NewEvalCase['kind'];
const KINDS: { value: Kind; label: string }[] = [
  { value: 'correct_result', label: 'Kết luận đúng' },
  { value: 'ask_at_right_time', label: 'Hỏi đúng lúc' },
  { value: 'respect_permission', label: 'Trong quyền dữ liệu' },
  { value: 'correct_format', label: 'Đúng khuôn trình bày' },
  { value: 'refusal', label: 'Từ chối đúng' },
];
const MANUAL = 'manual';

export function AddCaseDialog({
  open,
  agentId,
  evalSetId,
  onClose,
  onAdded,
}: {
  open: boolean;
  agentId: string;
  evalSetId: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const runs = useV1(open ? () => listRuns({ agent_id: agentId }) : null, [open, agentId]);
  const [source, setSource] = useState<string>(MANUAL);
  const [input, setInput] = useState('');
  const [expected, setExpected] = useState('');
  const [kind, setKind] = useState<Kind>('ask_at_right_time');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setSource(MANUAL);
    setInput('');
    setExpected('');
    setKind('ask_at_right_time');
    setError(undefined);
  }, [open]);

  if (!open) return null;

  // The mock ignores agent_id, so filter here too.
  const agentRuns = (runs.data?.items ?? []).filter((r) => r.agent_id === agentId);
  const sourceOptions = [
    ...agentRuns.map((r) => ({ value: r.id, label: `Lượt chạy ${r.code}` })),
    { value: MANUAL, label: 'Nhập tay' },
  ];

  const pickSource = (id: string) => {
    setSource(id);
    const run = agentRuns.find((r) => r.id === id);
    if (run && !input.trim()) setInput(run.title);
  };

  const submit = async () => {
    if (!input.trim() || !expected.trim()) {
      setError('Cần nhập đầu vào và kỳ vọng.');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      await addEvalCase(evalSetId, {
        source_run_id: source === MANUAL ? null : source,
        input: input.trim(),
        expected: expected.trim(),
        kind,
      });
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open
      title="Thêm tình huống thử"
      description="Tình huống từ lượt chạy thật giữ nguyên dữ liệu tại thời điểm đó."
      onClose={onClose}
      onSubmit={() => void submit()}
      submitLabel="Thêm vào bộ thử"
      submitting={sending}
      error={error}
    >
      <Field label="Lấy từ">
        <Select<string>
          label="Lấy từ"
          options={sourceOptions}
          value={source}
          onChange={pickSource}
        />
      </Field>
      <Field label="Đầu vào">
        <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-full" />
      </Field>
      <Field label="Kỳ vọng">
        <Input value={expected} onChange={(e) => setExpected(e.target.value)} className="w-full" />
      </Field>
      <Field label="Loại">
        <Select<Kind> label="Loại" options={KINDS} value={kind} onChange={setKind} />
      </Field>
    </Dialog>
  );
}
