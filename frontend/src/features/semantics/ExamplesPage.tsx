import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  addExample,
  listExamples,
  listSynonyms,
  updateExampleSql,
  verifyExample,
  type ExampleQuery,
} from '../../lib/v1/examples';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Câu hỏi mẫu & đồng nghĩa" (2301:19194, UC-07-02): the example questions the agent
 * learns from (each with the SQL that answers it correctly) and the other names people use.
 * The selected example — by default the first one waiting for review — opens on the right with
 * why the agent suggested it, its SQL and what running it gave; it is corrected or marked right
 * there. Verified examples join the standard question set used to evaluate the context layer.
 * Suggestions arrive on their own, from real questions reported wrong; there is no button asking
 * the agent for them.
 */
const fieldClass =
  'rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small';

export default function ExamplesPage() {
  const { dataSpace } = useAppState();
  const examples = useV1(listExamples, []);
  const synonyms = useV1(listSynonyms, []);
  const [selectedId, setSelectedId] = useState<string>();
  const [adding, setAdding] = useState(false);

  const items = examples.data?.items ?? [];
  const scope = dataSpace?.name ?? 'Data Space này';
  const exampleView = useTableView(items, {
    search: (e) => `${e.question} ${e.usage_text ?? ''} ${e.source_label}`,
    filters: [
      {
        id: 'status',
        label: 'Trạng thái',
        value: (e) => (e.status === 'verified' ? 'Đã kiểm tra' : 'Chờ xem'),
      },
    ],
    sorts: [
      { id: 'question', value: (e) => e.question },
      { id: 'usage', value: (e) => e.usage_text },
      { id: 'source', value: (e) => e.source_label },
      { id: 'status', value: (e) => (e.status === 'verified' ? 'Đã kiểm tra' : 'Chờ xem') },
    ],
    pageSize: 50,
  });
  const synonymView = useTableView(synonyms.data?.items ?? [], {
    search: (s) => `${s.phrases.join(' ')} ${s.target_text} ${s.scope_label ?? ''}`,
    filters: [{ id: 'scope', label: 'Phạm vi', value: (s) => s.scope_label ?? scope }],
    sorts: [
      { id: 'phrases', value: (s) => s.phrases.join(', ') },
      { id: 'target', value: (s) => s.target_text },
      { id: 'scope', value: (s) => s.scope_label ?? scope },
    ],
    pageSize: 50,
  });
  const selected =
    items.find((e) => e.id === selectedId) ?? items.find((e) => e.status === 'pending') ?? items[0];

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Ngữ nghĩa', 'Câu hỏi mẫu']}
        title="Câu hỏi mẫu và cách gọi khác"
        description={
          examples.data && synonyms.data
            ? `Agent dùng câu hỏi mẫu kèm SQL đúng để hiểu câu hỏi mới; ${items.length} câu mẫu · ${synonyms.data.items.length} cách gọi khác trong ${scope}`
            : 'Agent dùng câu hỏi mẫu kèm SQL đúng để hiểu câu hỏi mới'
        }
        actions={
          <Button kind="primary" onClick={() => setAdding(true)}>
            Thêm câu hỏi mẫu
          </Button>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg pl-da-3xl pr-da-2xl pt-da-xl">
          <h2 className="text-foreground da-heading-3">Câu hỏi mẫu</h2>
          <AsyncRegion state={examples} what="câu hỏi mẫu">
            {() => (
              <>
              <TableToolbar view={exampleView} placeholder="Tìm câu hỏi…" searchLabel="Tìm câu hỏi mẫu" />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh view={exampleView} column="question" className="px-da-lg py-da-md font-normal">
                      Câu hỏi
                    </SortableTh>
                    <SortableTh view={exampleView} column="usage" className="w-[200px] px-da-lg py-da-md font-normal">
                      Dùng
                    </SortableTh>
                    <SortableTh view={exampleView} column="source" className="w-[150px] px-da-lg py-da-md font-normal">
                      Nguồn
                    </SortableTh>
                    <SortableTh view={exampleView} column="status" className="w-[150px] px-da-lg py-da-md font-normal">
                      Trạng thái
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {exampleView.rows.map((e) => (
                    <tr
                      key={e.id}
                      aria-selected={e.id === selected?.id}
                      onClick={() => setSelectedId(e.id)}
                      className={cn(
                        'cursor-pointer border-b border-border hover:bg-subtle',
                        e.id === selected?.id && 'bg-subtle',
                      )}
                    >
                      <td className="px-da-lg py-da-md">
                        <button
                          type="button"
                          onClick={() => setSelectedId(e.id)}
                          className="text-left text-foreground da-body-strong"
                        >
                          {e.question}
                        </button>
                      </td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                        {e.usage_text ?? '—'}
                      </td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                        {e.source_label}
                      </td>
                      <td className="px-da-lg py-da-md">
                        <StatusDot tone={e.status === 'verified' ? 'success' : 'warning'}>
                          {e.status === 'verified' ? 'Đã kiểm tra' : 'Chờ xem'}
                        </StatusDot>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <TablePager view={exampleView} />
              </>
            )}
          </AsyncRegion>

          <h2 className="text-foreground da-heading-3">Cách gọi khác</h2>
          <AsyncRegion state={synonyms} what="cách gọi khác">
            {() => (
              <>
              <TableToolbar view={synonymView} placeholder="Tìm cách gọi…" searchLabel="Tìm cách gọi" />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh view={synonymView} column="phrases" className="px-da-lg py-da-md font-normal">
                      Người dùng nói
                    </SortableTh>
                    <SortableTh view={synonymView} column="target" className="w-[260px] px-da-lg py-da-md font-normal">
                      Hệ thống hiểu là
                    </SortableTh>
                    <SortableTh view={synonymView} column="scope" className="w-[150px] px-da-lg py-da-md font-normal">
                      Phạm vi
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {synonymView.rows.map((s) => (
                    <tr key={s.id} className="border-b border-border">
                      <td className="px-da-lg py-da-md text-foreground da-body-small">
                        {s.phrases.map((p) => `“${p}”`).join(', ')}
                      </td>
                      <td className="px-da-lg py-da-md text-foreground da-mono">{s.target_text}</td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                        {s.scope_label ?? scope}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <TablePager view={synonymView} />
              </>
            )}
          </AsyncRegion>
        </div>
        {selected && (
          <ExampleDetail key={selected.id} example={selected} onChanged={examples.reload} />
        )}
      </div>
      {adding && (
        <AddExampleDialog
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            examples.reload();
          }}
        />
      )}
    </div>
  );
}

function ExampleDetail({ example, onChanged }: { example: ExampleQuery; onChanged: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [sql, setSql] = useState(example.sql);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const run = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError(undefined);
    try {
      await action();
      toast({ tone: 'success', message });
      setEditing(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      aria-label="Câu hỏi mẫu đang xem"
      className="flex w-[380px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden"
    >
      <h2 className="text-foreground da-heading-3">{example.question}</h2>
      {example.reason && <p className="text-muted-foreground da-body-small">{example.reason}</p>}
      {editing ? (
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">SQL</span>
          <textarea
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={5}
            className={cn(fieldClass, 'da-mono')}
          />
        </label>
      ) : (
        <pre className="whitespace-pre-wrap rounded-sm border border-border bg-card px-da-lg py-[10px] text-foreground da-mono">
          {example.sql}
        </pre>
      )}
      {example.result_text && (
        <p className="text-foreground da-body-small">{example.result_text}</p>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-da-md">
        {editing ? (
          <>
            <Button kind="ghost" disabled={busy} onClick={() => setEditing(false)}>
              Huỷ
            </Button>
            <Button
              kind="primary"
              loading={busy}
              onClick={() => run(() => updateExampleSql(example.id, sql), 'Đã lưu SQL')}
            >
              Lưu SQL
            </Button>
          </>
        ) : (
          <>
            <Button kind="ghost" onClick={() => setEditing(true)}>
              Sửa
            </Button>
            {example.status === 'pending' && (
              <Button
                kind="primary"
                loading={busy}
                onClick={() => run(() => verifyExample(example.id), 'Đã đánh dấu đúng')}
              >
                Đánh dấu đúng
              </Button>
            )}
          </>
        )}
      </div>
      <p className="text-muted-foreground da-caption">
        Câu hỏi mẫu được dùng trong bộ câu hỏi chuẩn khi đánh giá lớp ngữ cảnh.
      </p>
    </aside>
  );
}

function AddExampleDialog({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const toast = useToast();
  const [question, setQuestion] = useState('');
  const [sql, setSql] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const add = async () => {
    if (!question.trim() || !sql.trim()) {
      setError('Nhập câu hỏi và SQL trả lời đúng.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await addExample({ question: question.trim(), sql: sql.trim() });
      toast({ tone: 'success', message: 'Đã thêm câu hỏi mẫu' });
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open
      title="Thêm câu hỏi mẫu"
      confirmLabel="Thêm"
      tone="primary"
      loading={busy}
      error={error}
      onCancel={onClose}
      onConfirm={add}
    >
      <div className="flex flex-col gap-da-md">
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Câu hỏi</span>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            className={fieldClass}
          />
        </label>
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">SQL trả lời đúng</span>
          <textarea
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            rows={4}
            className={cn(fieldClass, 'da-mono')}
          />
        </label>
        <p className="text-muted-foreground da-caption">SQL được chạy thử trước khi lưu.</p>
      </div>
    </ConfirmDialog>
  );
}
