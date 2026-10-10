import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  createTerm,
  listGlossary,
  listUnknownPhrases,
  type GlossaryEntry,
} from '../../lib/v1/semanticLayer';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Semantic · Từ điển nghiệp vụ" (2088:7308, UC-07-09): how the agent reads the
 * organisation's words — each term points to a metric, entity, attribute or filter, with its
 * synonyms, where it applies and whether it is certified. The same word can mean different things
 * in different Data Spaces; an ambiguous term shows its meanings on the right, which one is the
 * default and what the agent does when unsure. Below, phrases the agent did not understand in 30
 * days, each with a suggestion that "Thêm" turns into a draft term.
 */
const STATUS = {
  certified: { label: 'Chứng nhận', tone: 'success' },
  ambiguous: { label: 'Mơ hồ', tone: 'warning' },
  draft: { label: 'Nháp', tone: 'neutral' },
} as const;

export default function GlossaryPage() {
  const toast = useToast();
  const terms = useV1(listGlossary, []);
  const unknown = useV1(listUnknownPhrases, []);
  const [picked, setPicked] = useState<string>();
  const [adding, setAdding] = useState(false);
  const [term, setTerm] = useState('');
  const [definition, setDefinition] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const items = terms.data?.items ?? [];
  const selected =
    items.find((t) => t.id === picked) ?? items.find((t) => t.status === 'ambiguous') ?? items[0];

  const add = async (key: string, body: { term: string; definition: string }) => {
    setBusy(key);
    setError(undefined);
    try {
      const created = await createTerm(body);
      toast({ tone: 'success', message: `Đã thêm “${created.term}” ở trạng thái nháp` });
      setAdding(false);
      setTerm('');
      setDefinition('');
      terms.reload();
      unknown.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl pb-da-2xl pt-da-lg">
        <PageHeader
          breadcrumb={['Dữ liệu', 'Ngữ nghĩa', 'Từ điển nghiệp vụ']}
          title={`Từ điển nghiệp vụ · ${items.length} thuật ngữ`}
          description="Cách agent hiểu ngôn ngữ của tổ chức: mỗi thuật ngữ trỏ tới một chỉ số, thực thể, thuộc tính hoặc bộ lọc. Cùng một từ có thể mang nghĩa khác nhau ở từng Data Space."
          actions={
            <>
              <Tooltip content="Nhập thuật ngữ từ tài liệu đang được làm.">
                <Button kind="ghost" disabled>
                  Nhập từ tài liệu
                </Button>
              </Tooltip>
              <Button
                kind="primary"
                onClick={() => {
                  setError(undefined);
                  setAdding(true);
                }}
              >
                Thêm thuật ngữ
              </Button>
            </>
          }
        />
        {error && !adding && <Notice tone="error">Không thêm được: {error}</Notice>}
        <AsyncRegion state={terms} what="từ điển" skeletonRows={6}>
          {() => (
            <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
              <thead>
                <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                  <th className="w-[170px] px-da-sm py-da-sm font-normal">Thuật ngữ</th>
                  <th className="px-da-sm py-da-sm font-normal">Ánh xạ tới · Từ đồng nghĩa</th>
                  <th className="w-[138px] px-da-sm py-da-sm font-normal">Data Space</th>
                  <th className="w-[118px] px-da-sm py-da-sm font-normal">Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr
                    key={t.id}
                    className={cn(
                      'border-b border-border text-foreground last:border-b-0',
                      selected?.id === t.id && 'bg-accent',
                    )}
                  >
                    <td className="px-da-sm py-[7px]">
                      <button
                        type="button"
                        onClick={() => setPicked(t.id)}
                        className="text-left hover:underline"
                      >
                        {t.term}
                      </button>
                    </td>
                    <td className="truncate px-da-sm py-[7px]">
                      <StatusDot tone="success">{t.maps_to}</StatusDot>{' '}
                      <span className="text-muted-foreground">{t.synonyms}</span>
                    </td>
                    <td className="truncate px-da-sm py-[7px]">{t.data_space_text}</td>
                    <td className="px-da-sm py-[7px]">
                      <StatusDot tone={STATUS[t.status].tone}>{STATUS[t.status].label}</StatusDot>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <section
          aria-labelledby="unknown-title"
          className="flex flex-col gap-da-sm rounded-md border border-border p-da-md"
        >
          <h2 id="unknown-title" className="flex items-center gap-da-sm text-info da-body-small">
            <Icon name="agent" />
            Câu hỏi agent chưa hiểu trong 30 ngày
          </h2>
          <AsyncRegion state={unknown} what="câu hỏi chưa hiểu">
            {(page) =>
              page.items.length === 0 ? (
                <p className="text-muted-foreground da-body-small">
                  Agent hiểu mọi câu hỏi gần đây.
                </p>
              ) : (
                <ul className="flex flex-col gap-da-sm">
                  {page.items.map((u) => (
                    <li key={u.id} className="flex items-center gap-da-sm da-body-small">
                      <span className="text-foreground da-mono">“{u.phrase}”</span>
                      <span className="text-muted-foreground da-caption">{u.count} lần</span>
                      <span className="min-w-0 flex-1 text-foreground">{u.suggestion}</span>
                      <Button
                        loading={busy === u.id}
                        disabled={busy !== null}
                        onClick={() => add(u.id, u.draft)}
                        aria-label={`Thêm “${u.phrase}”`}
                      >
                        Thêm
                      </Button>
                    </li>
                  ))}
                </ul>
              )
            }
          </AsyncRegion>
        </section>
      </div>
      {selected && <TermPanel term={selected} />}
      <Dialog
        open={adding}
        size="sm"
        title="Thêm thuật ngữ"
        submitLabel="Thêm"
        submitting={busy === 'new'}
        error={adding ? error : undefined}
        onClose={() => setAdding(false)}
        onSubmit={() => add('new', { term: term.trim(), definition: definition.trim() })}
      >
        <div className="flex flex-col gap-da-md">
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Thuật ngữ</span>
            <input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              className="h-9 rounded-md border border-border-strong bg-card px-da-md text-foreground da-body-small"
            />
          </label>
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Ánh xạ tới</span>
            <input
              value={definition}
              onChange={(e) => setDefinition(e.target.value)}
              placeholder="Chỉ số, thực thể, thuộc tính hoặc bộ lọc"
              className="h-9 rounded-md border border-border-strong bg-card px-da-md text-foreground da-body-small"
            />
          </label>
          <p className="text-muted-foreground da-caption">
            Thuật ngữ mới ở trạng thái nháp cho tới khi chủ sở hữu chứng nhận.
          </p>
        </div>
      </Dialog>
    </div>
  );
}

function TermPanel({ term }: { term: GlossaryEntry }) {
  const status = STATUS[term.status];
  return (
    <aside
      aria-label={`Thuật ngữ ${term.term}`}
      className="flex w-[340px] shrink-0 flex-col gap-da-md border-l border-border px-da-lg pt-da-lg max-[1023px]:w-full max-[1023px]:border-l-0"
    >
      <div className="flex items-center gap-da-sm">
        <h2 className="min-w-0 flex-1 text-foreground da-heading-2">{term.term}</h2>
        <StatusDot tone={status.tone}>{status.label}</StatusDot>
      </div>
      {term.meanings.length > 0 ? (
        <>
          <p className="text-muted-foreground da-body-small">
            Có {term.meanings.length} nghĩa đang dùng trong tổ chức
          </p>
          {term.meanings.map((m) => (
            <div
              key={m.label}
              className={cn(
                'flex flex-col gap-da-xs rounded-md border p-da-sm',
                m.default ? 'border-primary' : 'border-border',
              )}
            >
              <div className="flex items-center gap-da-sm">
                <span className="min-w-0 flex-1 text-foreground da-body-small">{m.label}</span>
                {m.default && <StatusDot tone="success">Mặc định</StatusDot>}
              </div>
              <span className="text-foreground da-mono">{m.expression}</span>
            </div>
          ))}
        </>
      ) : (
        <p className="text-foreground da-body-small">
          Trỏ tới <span className="da-mono">{term.maps_to}</span>
          {term.synonyms && `; đồng nghĩa: ${term.synonyms}`}.
        </p>
      )}
      <dl className="flex flex-col gap-da-sm da-body-small">
        {term.ambiguity_rule && <Pair label="Khi mơ hồ">{term.ambiguity_rule}</Pair>}
        <Pair label="Chủ sở hữu">{term.owner_name}</Pair>
        {term.usage_text && <Pair label="Dùng 30 ngày">{term.usage_text}</Pair>}
      </dl>
    </aside>
  );
}

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-da-md">
      <dt className="w-[96px] shrink-0 text-muted-foreground da-caption">{label}</dt>
      <dd className="text-foreground">{children}</dd>
    </div>
  );
}
