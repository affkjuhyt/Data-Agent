import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  createSkillFromSession,
  getSessionSkillDraft,
  type SessionSkillDraft,
} from '../../lib/v1/skillAuthoring';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P03 · Tạo kỹ năng từ phiên" (2294:18171, UC-03-03), opened from "Biến phiên thành agent" →
 * "Lưu thành kỹ năng" in a session notebook. The agent proposes which cells become steps (cells
 * that only present or annotate are left out) and how fixed values become parameters; the author
 * ticks the cells, names the skill and saves it as a draft, then lands on its validation. The
 * session's own result is kept as the first test case. A skill only calls certified metrics.
 */
export default function SkillFromSessionPage() {
  const { routeId } = useAppState();
  const draft = useV1(routeId ? () => getSessionSkillDraft(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={draft} what="bản nháp kỹ năng">
      {(d) => <FromSession key={d.session_id} draft={d} />}
    </AsyncRegion>
  );
}

function FromSession({ draft }: { draft: SessionSkillDraft }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [kept, setKept] = useState(
    () => new Set(draft.cells.filter((c) => c.kept).map((c) => c.seq)),
  );
  const [name, setName] = useState(draft.name);
  const [description, setDescription] = useState(draft.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const toggle = (seq: number, on: boolean) =>
    setKept((prev) => {
      const next = new Set(prev);
      if (on) next.add(seq);
      else next.delete(seq);
      return next;
    });

  const save = async () => {
    if (!name.trim()) return setError('Đặt tên cho kỹ năng trước khi lưu.');
    if (kept.size === 0) return setError('Giữ lại ít nhất một ô của phiên.');
    setBusy(true);
    setError(undefined);
    try {
      const created = await createSkillFromSession({
        session_id: draft.session_id,
        kept_cells: [...kept].sort((a, b) => a - b),
        name: name.trim(),
        description: description.trim(),
      });
      toast({ tone: 'success', message: `Đã lưu bản nháp v${created.version}; đang kiểm định` });
      navigate('agents', 'SKILL_TESTS', created.skill_id);
    } catch (e) {
      setError(`Không lưu được: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', `Tạo từ phiên “${draft.session_title}”`]}
        title="Tạo kỹ năng từ phiên"
        actions={
          <>
            <Button kind="ghost" onClick={() => navigate('analysis', 'SESSIONS', draft.session_id)}>
              Huỷ
            </Button>
            <Button kind="primary" loading={busy} onClick={save}>
              Lưu bản nháp và kiểm định
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <section className="flex min-w-0 flex-1 flex-col gap-[10px] py-da-xl pl-da-3xl pr-da-2xl">
          {error && <Notice tone="error">{error}</Notice>}
          <div className="flex items-center gap-da-md">
            <h2 className="min-w-0 flex-1 text-foreground da-heading-3">Chọn các bước giữ lại</h2>
            <span className="text-muted-foreground da-caption">
              {kept.size}/{draft.cells.length} ô của phiên
            </span>
          </div>
          <ul className="flex flex-col border-b border-border">
            {draft.cells.map((cell) => {
              const on = kept.has(cell.seq);
              return (
                <li
                  key={cell.seq}
                  className="flex items-start gap-da-lg border-b border-border px-da-lg py-[10px] last:border-b-0"
                >
                  <Checkbox checked={on} onChange={(v) => toggle(cell.seq, v)}>
                    <span className="sr-only">Giữ ô {cell.seq}</span>
                  </Checkbox>
                  <span className="w-5 shrink-0 text-muted-foreground da-mono">{cell.seq}</span>
                  <span
                    className={cn(
                      'flex min-w-0 flex-1 flex-col gap-[2px]',
                      on ? 'text-foreground' : 'text-muted-foreground',
                    )}
                  >
                    <span className="da-body-strong">{cell.title}</span>
                    <span
                      className={cn(
                        'da-mono',
                        on && cell.param_text ? 'text-primary' : 'text-muted-foreground',
                      )}
                    >
                      {cell.param_text ?? cell.note ?? '—'}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>

        <aside className="flex w-[380px] shrink-0 flex-col gap-[14px] border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t">
          <Field label="Tên">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Mô tả một câu">
            <Textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full"
            />
          </Field>
          <h2 className="text-foreground da-heading-3">Tham số agent nhận ra</h2>
          <ParamTable params={draft.params} />
          <h2 className="text-foreground da-heading-3">Ca kiểm thử tạo sẵn</h2>
          <p className="text-foreground da-body-small">{draft.test_case_text}</p>
        </aside>
      </div>
    </div>
  );
}

function ParamTable({ params }: { params: SessionSkillDraft['params'] }) {
  const view = useTableView(params, {
    search: (p) => `${p.name} ${p.type_label} ${p.default_text}`,
    filters: [{ id: 'type', label: 'Kiểu', value: (p) => p.type_label }],
    sorts: [
      { id: 'name', value: (p) => p.name },
      { id: 'type', value: (p) => p.type_label },
      { id: 'default', value: (p) => p.default_text },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm tham số…" searchLabel="Tìm tham số" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="name" className="w-[110px] px-da-lg py-da-md font-normal">
              Tên
            </SortableTh>
            <SortableTh view={view} column="type" className="px-da-lg py-da-md font-normal">
              Kiểu
            </SortableTh>
            <SortableTh view={view} column="default" className="w-[110px] px-da-lg py-da-md font-normal">
              Mặc định
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((p) => (
            <tr key={p.name} className="border-b border-border">
              <td className="px-da-lg py-[9px] text-foreground da-mono">{p.name}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">{p.type_label}</td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">{p.default_text}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
