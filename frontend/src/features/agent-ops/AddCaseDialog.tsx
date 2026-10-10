import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { addEvalCase, type EvalCase, type EvalCaseKind } from '../../lib/v1/agentOps';
import { listRuns } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Hộp thoại · addTest" (2095:9006): add a case to the eval set, optionally frozen from a real
 * run. The design system has ConfirmDialog only, so this form dialog reuses its frame (scrim,
 * radius-xl, shadow-lg, Esc closes, focus kept inside) at the 560px of the Figma frame.
 */
const KINDS: { value: EvalCaseKind; label: string }[] = [
  { value: 'correct_conclusion', label: 'Kết luận đúng' },
  { value: 'ask_on_time', label: 'Hỏi đúng lúc' },
  { value: 'permission', label: 'Trong quyền dữ liệu' },
  { value: 'cost', label: 'Chi phí' },
  { value: 'latency', label: 'Thời gian' },
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
  onAdded: (created: EvalCase) => void;
}) {
  const runs = useV1(open ? () => listRuns({ agent_id: agentId }) : null, [open, agentId]);
  const [source, setSource] = useState<string>(MANUAL);
  const [input, setInput] = useState('');
  const [expected, setExpected] = useState('');
  const [kind, setKind] = useState<EvalCaseKind>('ask_on_time');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    setSource(MANUAL);
    setInput('');
    setExpected('');
    setKind('ask_on_time');
    setError(undefined);
    dialogRef.current?.querySelector<HTMLElement>('button[role="combobox"]')?.focus();
    return () => previous?.focus();
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
      const created = await addEvalCase(evalSetId, {
        source_run_id: source === MANUAL ? null : source,
        input: input.trim(),
        expected: expected.trim(),
        kind,
      });
      onAdded(created);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape' && !event.defaultPrevented) {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([aria-disabled="true"]), input',
    );
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[var(--z-dialog)] flex items-center justify-center bg-[rgb(23_33_43/0.32)]"
      onMouseDown={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-case-title"
        onKeyDown={onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex w-[560px] max-w-[calc(100vw-32px)] flex-col gap-da-xl rounded-xl bg-card p-da-xl shadow-lg"
      >
        <div className="flex items-start">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id="add-case-title" className="text-foreground da-heading-2">
              Thêm tình huống thử
            </h2>
            <p className="text-muted-foreground da-caption">
              Tình huống từ lượt chạy thật giữ nguyên dữ liệu tại thời điểm đó.
            </p>
          </div>
          <button
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            className="shrink-0 text-foreground"
          >
            <Icon name="x" />
          </button>
        </div>
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
          <Input
            value={expected}
            onChange={(e) => setExpected(e.target.value)}
            className="w-full"
          />
        </Field>
        <Field label="Loại">
          <Select<EvalCaseKind> label="Loại" options={KINDS} value={kind} onChange={setKind} />
        </Field>
        {error && (
          <p role="alert" className="text-error da-caption">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-da-md">
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={sending} onClick={submit}>
            Thêm vào bộ thử
          </Button>
        </div>
      </div>
    </div>
  );
}
