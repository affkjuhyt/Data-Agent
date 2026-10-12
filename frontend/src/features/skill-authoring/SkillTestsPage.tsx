import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  addSkillTestCase,
  applySkillFix,
  getLatestTestRun,
  runSkillTests,
  submitSkill,
  type SkillTestRun,
} from '../../lib/v1/skillAuthoring';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P03 · Kiểm định kỹ năng" (2294:18369, UC-03-04): the skill's test cases run on locked data
 * (so a result repeats), each with what was expected and what came out. A failing case opens on
 * the right with why it failed and the fix the agent proposes; "Áp dụng và chạy lại" applies it
 * and reruns the suite. "Gửi duyệt" needs every case to pass or each failing case to carry a note
 * for the reviewer — the note field is not in the frame, the rule under it is.
 */
const time = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

export default function SkillTestsPage() {
  const { routeId } = useAppState();
  const run = useV1(routeId ? () => getLatestTestRun(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={run} what="kết quả kiểm định">
      {(r) => <Tests key={r.skill_id} initial={r} />}
    </AsyncRegion>
  );
}

function Tests({ initial }: { initial: SkillTestRun }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [run, setRun] = useState(initial);
  const [selected, setSelected] = useState(() => initial.failures[0]?.case_id);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<'test' | 'fix' | 'submit' | null>(null);
  const [error, setError] = useState<string>();
  const [adding, setAdding] = useState(false);

  const id = run.skill_id;
  const passed = run.cases.filter((c) => c.passed).length;
  const failing = run.cases.filter((c) => !c.passed);
  const unexplained = failing.filter((c) => !notes[c.id]?.trim());
  const failure = run.failures.find((f) => f.case_id === selected) ?? run.failures[0];
  const failedCase = failure && run.cases.find((c) => c.id === failure.case_id);

  const act = async (verb: 'test' | 'fix' | 'submit') => {
    setBusy(verb);
    setError(undefined);
    try {
      if (verb === 'submit') {
        await submitSkill(id, notes);
        toast({ tone: 'success', message: `Đã gửi duyệt ${run.skill_name} v${run.version}` });
        navigate('agents', 'SKILL_VERSIONS', id);
        return;
      }
      const next =
        verb === 'test' ? await runSkillTests(id) : await applySkillFix(id, failure!.case_id);
      setRun(next);
      setSelected(next.failures[0]?.case_id);
      const ok = next.cases.filter((c) => c.passed).length;
      toast({ tone: 'info', message: `Đã chạy lại · ${ok}/${next.cases.length} đạt` });
    } catch (e) {
      setError(`Không thực hiện được: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const submitBlocker =
    run.status === 'pending_review'
      ? 'Bản này đang chờ duyệt.'
      : unexplained.length
        ? `Sửa hoặc ghi chú ca lỗi “${unexplained[0].name}” trước khi gửi duyệt.`
        : null;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', run.skill_name, `v${run.version}`]}
        title={
          <span className="inline-flex items-center gap-[10px]">
            Kiểm định · {run.skill_name} v{run.version}
            <StatusDot tone={failing.length ? 'warning' : 'success'}>
              {passed}/{run.cases.length} đạt
            </StatusDot>
          </span>
        }
        description={`Chạy ${run.cases.length} ca kiểm thử trên dữ liệu đã chốt · ${passed} đạt · lần chạy ${time(run.ran_at)}`}
        actions={
          <>
            <Button kind="ghost" onClick={() => setAdding(true)}>
              Thêm ca kiểm thử
            </Button>
            <Button loading={busy === 'test'} disabled={busy !== null} onClick={() => act('test')}>
              Chạy lại
            </Button>
            {submitBlocker ? (
              <Tooltip content={submitBlocker}>
                <Button kind="primary" disabled>
                  Gửi duyệt
                </Button>
              </Tooltip>
            ) : (
              <Button
                kind="primary"
                loading={busy === 'submit'}
                disabled={busy !== null}
                onClick={() => act('submit')}
              >
                Gửi duyệt
              </Button>
            )}
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <section className="flex min-w-0 flex-1 flex-col gap-da-lg pl-da-3xl pr-da-2xl pt-da-xl">
          {error && <Notice tone="error">{error}</Notice>}
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Ca</th>
                <th className="w-[110px] px-da-lg py-da-md font-normal">Kỳ</th>
                <th className="w-[170px] px-da-lg py-da-md font-normal">Kỳ vọng</th>
                <th className="w-[170px] px-da-lg py-da-md font-normal">Thực tế</th>
                <th className="w-[100px] px-da-lg py-da-md font-normal">Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {run.cases.map((c) => (
                <tr
                  key={c.id}
                  className={cn(
                    'border-b border-border',
                    !c.passed && c.id === failure?.case_id && 'bg-subtle',
                  )}
                >
                  <td className="px-da-lg py-[9px] text-foreground da-body-strong">
                    {c.passed ? (
                      c.name
                    ) : (
                      <button
                        type="button"
                        aria-pressed={c.id === failure?.case_id}
                        onClick={() => setSelected(c.id)}
                        className="text-left hover:underline"
                      >
                        {c.name}
                      </button>
                    )}
                  </td>
                  <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                    {c.period_text}
                  </td>
                  <td className="px-da-lg py-[9px] text-foreground da-body-small">{c.expected}</td>
                  <td
                    className={cn(
                      'px-da-lg py-[9px] da-body-small',
                      c.passed ? 'text-foreground' : 'text-error',
                    )}
                  >
                    {c.actual}
                  </td>
                  <td className="px-da-lg py-[9px]">
                    <StatusDot tone={c.passed ? 'success' : 'error'}>
                      {c.passed ? 'Đạt' : 'Lỗi'}
                    </StatusDot>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <aside className="flex w-[380px] shrink-0 flex-col items-start gap-[10px] border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t">
          {failure && failedCase ? (
            <>
              <h2 className="text-foreground da-heading-3">Ca “{failedCase.name}” lỗi</h2>
              <p className="text-foreground da-body-small">{failure.explanation}</p>
              <h2 className="text-foreground da-heading-3">Agent đề xuất sửa</h2>
              <pre className="w-full whitespace-pre-wrap rounded-sm border border-border bg-card px-da-lg py-[10px] text-foreground da-mono">
                {failure.fix_diff.join('\n')}
              </pre>
              <Button loading={busy === 'fix'} disabled={busy !== null} onClick={() => act('fix')}>
                Áp dụng và chạy lại
              </Button>
              <div className="w-full">
                <Field
                  label="Hoặc ghi chú cho người duyệt"
                  hint="Ca lỗi có ghi chú không chặn việc gửi duyệt."
                >
                  <Textarea
                    rows={2}
                    value={notes[failedCase.id] ?? ''}
                    onChange={(e) =>
                      setNotes((prev) => ({ ...prev, [failedCase.id]: e.target.value }))
                    }
                    className="w-full"
                  />
                </Field>
              </div>
            </>
          ) : (
            <>
              <h2 className="text-foreground da-heading-3">Mọi ca đều đạt</h2>
              <p className="text-foreground da-body-small">
                Kỹ năng đã sẵn sàng để gửi duyệt. Người duyệt khác người tạo sẽ xem phiên bản và kết
                quả kiểm định này.
              </p>
            </>
          )}
          <p className="text-muted-foreground da-caption">
            Gửi duyệt khi mọi ca đạt hoặc ca lỗi có ghi chú
          </p>
        </aside>
      </div>
      <AddCaseDialog
        open={adding}
        onClose={() => setAdding(false)}
        onAdd={async (body) => {
          const next = await addSkillTestCase(id, body);
          setRun(next);
          setAdding(false);
          toast({ tone: 'success', message: `Đã thêm ca “${body.name}”` });
        }}
      />
    </div>
  );
}

function AddCaseDialog({
  open,
  onClose,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  onAdd: (body: { name: string; period_text: string; expected: string }) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [period, setPeriod] = useState('');
  const [expected, setExpected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const submit = async () => {
    if (!name.trim() || !expected.trim()) return setError('Cần tên ca và kết quả kỳ vọng.');
    setBusy(true);
    setError(undefined);
    try {
      await onAdd({ name: name.trim(), period_text: period.trim(), expected: expected.trim() });
      setName('');
      setPeriod('');
      setExpected('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      title="Thêm ca kiểm thử"
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} onClick={submit}>
            Thêm ca
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Tên ca">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Kỳ" hint="Kỳ đã chốt, ví dụ T8→T9/2026">
        <Input value={period} onChange={(e) => setPeriod(e.target.value)} />
      </Field>
      <Field label="Kỳ vọng">
        <Input value={expected} onChange={(e) => setExpected(e.target.value)} />
      </Field>
    </Dialog>
  );
}
