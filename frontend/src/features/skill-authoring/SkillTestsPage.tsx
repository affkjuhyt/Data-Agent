import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  addSkillTestCase,
  getLatestTestRun,
  runSkillTests,
  TESTABLE,
  submitSkill,
  type SkillTestRun,
} from '../../lib/v1/skillAuthoring';
import { useV1, useV1Mutation } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P03 · Kiểm định kỹ năng" (2294:18369, UC-03-04): the skill's test cases run on locked data
 * (so a result repeats), each with what was expected and what came out. A failing case opens on
 * the right with why it failed and the fix the agent proposes. A2 has no route that applies the
 * fix, so "Áp dụng và chạy lại" stays disabled and the author edits the step in the editor, then
 * reruns the suite. "Gửi duyệt" needs every case to pass or each failing case to carry a note
 * for the reviewer — the note field is not in the frame, the rule under it is.
 */
const time = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

export default function SkillTestsPage() {
  const { routeId } = useAppState();
  const run = useV1(
    ['getLatestTestRun', routeId],
    routeId ? () => getLatestTestRun(routeId) : null,
  );
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
  const [adding, setAdding] = useState(false);

  const id = run.skill_id;
  const passed = run.cases.filter((c) => c.passed).length;
  const failing = run.cases.filter((c) => !c.passed);
  const unexplained = failing.filter((c) => !notes[c.id]?.trim());
  const failure = run.failures.find((f) => f.case_id === selected) ?? run.failures[0];
  const failedCase = failure && run.cases.find((c) => c.id === failure.case_id);
  // A2's eval results carry neither a period nor, for a passing case, what it expected: a column
  // that no case fills is left out.
  const periods = run.cases.some((c) => c.period_text);
  const expectations = run.cases.some((c) => c.expected);
  const addBlocker =
    run.eval_set_id === null
      ? 'Kỹ năng chưa có bộ ca kiểm thử: chạy kiểm định một lần để tạo bộ, rồi thêm ca.'
      : null;
  const cases = useTableView(run.cases, {
    search: (c) => `${c.name} ${c.period_text} ${c.expected} ${c.actual}`,
    filters: [{ id: 'result', label: 'Kết quả', value: (c) => (c.passed ? 'Đạt' : 'Chưa đạt') }],
    sorts: [
      { id: 'name', value: (c) => c.name },
      { id: 'period', value: (c) => c.period_text },
      { id: 'expected', value: (c) => c.expected },
      { id: 'actual', value: (c) => c.actual },
      { id: 'result', value: (c) => (c.passed ? 'Đạt' : 'Chưa đạt') },
    ],
    pageSize: 50,
  });

  const retest = useV1Mutation(() => runSkillTests(run), {
    onSuccess: (next) => {
      setRun(next);
      setSelected(next.failures[0]?.case_id);
      const ok = next.cases.filter((c) => c.passed).length;
      toast({
        tone: 'info',
        message:
          next.status === 'testing' && next.cases.length === 0
            ? `Đã gửi v${next.version} chạy kiểm định; kết quả hiện khi bộ đánh giá chấm xong.`
            : `Đã chạy lại · ${ok}/${next.cases.length} đạt`,
      });
    },
  });
  const submit = useV1Mutation(() => submitSkill(run), {
    onSuccess: () => {
      toast({ tone: 'success', message: `Đã gửi duyệt ${run.skill_name} v${run.version}` });
      navigate('agents', 'SKILL_VERSIONS', id);
    },
  });
  // One error line for both actions: starting one clears the other's.
  const busy = retest.pending ? 'test' : submit.pending ? 'submit' : null;
  const failed = retest.error ?? submit.error;
  const error = failed && `Không thực hiện được: ${failed.message}`;
  const act = (verb: 'test' | 'submit') => {
    if (verb === 'test') {
      submit.reset();
      retest.run();
    } else {
      retest.reset();
      submit.run();
    }
  };

  // skill/routes.py: only a draft (or a version flagged needs_review) is sent to its tests, and
  // only a tested version is sent to review; the buttons say so rather than answer 409.
  const rerunBlocker = TESTABLE.has(run.status)
    ? null
    : `v${run.version} đã gửi kiểm định; sửa trong trình soạn để có bản nháp mới rồi chạy lại.`;
  const submitBlocker =
    run.status === 'pending_review'
      ? 'Bản này đang chờ duyệt.'
      : run.status !== 'testing'
        ? `v${run.version} chưa qua kiểm định; chạy kiểm định trước khi gửi duyệt.`
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
            {run.cases.length === 0 ? (
              <StatusDot tone="idle">chưa chạy</StatusDot>
            ) : (
              <StatusDot tone={failing.length ? 'warning' : 'success'}>
                {passed}/{run.cases.length} đạt
              </StatusDot>
            )}
          </span>
        }
        description={
          run.ran_at
            ? `Chạy ${run.cases.length} ca kiểm thử trên dữ liệu đã chốt · ${passed} đạt · lần chạy ${time(run.ran_at)}`
            : typeof run.case_count === 'number'
              ? `Bộ có ${run.case_count} ca kiểm thử; phiên bản này chưa chạy kiểm định trên dữ liệu đã chốt.`
              : 'Phiên bản này chưa chạy kiểm định trên dữ liệu đã chốt.'
        }
        actions={
          <>
            {addBlocker ? (
              <Tooltip content={addBlocker}>
                <Button kind="ghost" disabled>
                  Thêm ca kiểm thử
                </Button>
              </Tooltip>
            ) : (
              <Button kind="ghost" onClick={() => setAdding(true)}>
                Thêm ca kiểm thử
              </Button>
            )}
            {rerunBlocker ? (
              <Tooltip content={rerunBlocker}>
                <Button disabled>Chạy lại</Button>
              </Tooltip>
            ) : (
              <Button
                loading={busy === 'test'}
                disabled={busy !== null}
                onClick={() => act('test')}
              >
                Chạy lại
              </Button>
            )}
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
          <TableToolbar view={cases} placeholder="Tìm ca kiểm thử…" searchLabel="Tìm ca kiểm thử" />
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <SortableTh view={cases} column="name" className="px-da-lg py-da-md font-normal">
                  Ca
                </SortableTh>
                {periods && (
                  <SortableTh
                    view={cases}
                    column="period"
                    className="w-[110px] px-da-lg py-da-md font-normal"
                  >
                    Kỳ
                  </SortableTh>
                )}
                {expectations && (
                  <SortableTh
                    view={cases}
                    column="expected"
                    className="w-[170px] px-da-lg py-da-md font-normal"
                  >
                    Kỳ vọng
                  </SortableTh>
                )}
                <SortableTh
                  view={cases}
                  column="actual"
                  className="w-[170px] px-da-lg py-da-md font-normal"
                >
                  Thực tế
                </SortableTh>
                <SortableTh
                  view={cases}
                  column="result"
                  className="w-[100px] px-da-lg py-da-md font-normal"
                >
                  Kết quả
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {cases.rows.map((c) => (
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
                  {periods && (
                    <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                      {c.period_text}
                    </td>
                  )}
                  {expectations && (
                    <td className="px-da-lg py-[9px] text-foreground da-body-small">
                      {c.expected}
                    </td>
                  )}
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
          <TablePager view={cases} />
        </section>

        <aside className="flex w-[380px] shrink-0 flex-col items-start gap-[10px] border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t">
          {failure && failedCase ? (
            <>
              <h2 className="text-foreground da-heading-3">Ca “{failedCase.name}” lỗi</h2>
              <p className="text-foreground da-body-small">{failure.explanation}</p>
              {/* The proposed fix is PROPOSAL; A2's eval result gives only the cause. */}
              {failure.fix_diff.length > 0 && (
                <>
                  <h2 className="text-foreground da-heading-3">Agent đề xuất sửa</h2>
                  <pre className="w-full whitespace-pre-wrap rounded-sm border border-border bg-card px-da-lg py-[10px] text-foreground da-mono">
                    {failure.fix_diff.join('\n')}
                  </pre>
                </>
              )}
              <div className="flex flex-wrap items-center gap-da-sm">
                {failure.fix_diff.length > 0 && (
                  <Tooltip content="Chưa áp dụng tự động được đề xuất này.">
                    <Button disabled>Áp dụng và chạy lại</Button>
                  </Tooltip>
                )}
                <Button kind="ghost" onClick={() => navigate('agents', 'SKILL_EDITOR', id)}>
                  Sửa trong trình soạn
                </Button>
              </div>
              <p className="text-muted-foreground da-caption">
                {failure.fix_diff.length > 0
                  ? 'Hệ thống chưa áp dụng tự động đề xuất sửa: sửa bước trong trình soạn kỹ năng rồi bấm “Chạy lại”.'
                  : 'Sửa bước trong trình soạn kỹ năng rồi bấm “Chạy lại”.'}
              </p>
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
          ) : failing.length > 0 ? (
            <>
              <h2 className="text-foreground da-heading-3">{failing.length} ca chưa đạt</h2>
              <p className="text-foreground da-body-small">
                Kết quả kiểm định không kèm lý do cho các ca này. Sửa trong trình soạn rồi chạy lại.
              </p>
            </>
          ) : run.cases.length === 0 ? (
            <>
              <h2 className="text-foreground da-heading-3">Chưa có kết quả</h2>
              <p className="text-foreground da-body-small">
                Kết quả từng ca hiện ở đây sau khi phiên bản chạy kiểm định.
              </p>
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
        paramNames={run.param_names ?? []}
        onAdd={async (body) => {
          const next = await addSkillTestCase(run, body);
          setRun(next);
          setAdding(false);
          // A2 scores a new case at the next run of the suite, so it is not in the table yet.
          const listed = next.cases.some((c) => c.name === body.name);
          toast({
            tone: 'success',
            message: listed
              ? `Đã thêm ca “${body.name}”`
              : `Đã thêm ca “${body.name}”; ca được chấm ở lần kiểm định sau.`,
          });
        }}
      />
    </div>
  );
}

function AddCaseDialog({
  open,
  paramNames,
  onClose,
  onAdd,
}: {
  open: boolean;
  /** The skill's parameters, which the case runs with. */
  paramNames: string[];
  onClose: () => void;
  onAdd: (body: {
    name: string;
    period_text: string;
    expected: string;
    params: Record<string, string>;
  }) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [params, setParams] = useState<Record<string, string>>({});
  const [expected, setExpected] = useState('');
  // A case without a name or an expectation is caught here, before anything is sent.
  const [invalid, setInvalid] = useState<string>();
  const add = useV1Mutation(onAdd, {
    onSuccess: () => {
      setName('');
      setParams({});
      setExpected('');
    },
  });
  const error = invalid ?? add.error?.message;
  const submit = () => {
    if (!name.trim() || !expected.trim()) return setInvalid('Cần tên ca và kết quả kỳ vọng.');
    setInvalid(undefined);
    const given = Object.fromEntries(
      Object.entries(params)
        .map(([k, v]) => [k, v.trim()])
        .filter(([, v]) => v),
    );
    add.run({ name: name.trim(), period_text: '', expected: expected.trim(), params: given });
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
          <Button kind="primary" loading={add.pending} onClick={submit}>
            Thêm ca
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Tên ca">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      {paramNames.map((key) => (
        <Field key={key} label={key} hint="Tham số kỹ năng chạy với ca này">
          <Input
            value={params[key] ?? ''}
            onChange={(e) => setParams((prev) => ({ ...prev, [key]: e.target.value }))}
          />
        </Field>
      ))}
      <Field label="Kỳ vọng" hint="Kết luận của lượt chạy phải chứa đoạn này">
        <Input value={expected} onChange={(e) => setExpected(e.target.value)} />
      </Field>
    </Dialog>
  );
}
