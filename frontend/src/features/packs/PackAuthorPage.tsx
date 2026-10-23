import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  getPackDraft,
  listPackDrafts,
  publishPackVersion,
  validatePackVersion,
  type PackDraft,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Biên soạn gói" (2287:17550, UC-09-05): a draft pack version, for industry experts. Left,
 * what it holds and how much changed since the version it builds on; middle, the last test on the
 * sample data set — benchmark questions, metrics computable, check rules, and what did not pass;
 * right, the conditions for publishing. Publishing signs the version; organisations on the older
 * one are told, none is upgraded automatically. Opened from the rail, the first draft. The api
 * does not record the version a draft builds on, so what changed since it is not shown.
 */
const TONE = { success: 'text-success', warning: 'text-warning', neutral: 'text-foreground' };

export default function PackAuthorPage() {
  const { routeId } = useAppState();
  const list = useV1(['listPackDrafts', routeId], routeId ? null : listPackDrafts);
  const id = routeId || list.data?.items[0]?.id;
  const draft = useV1(['getPackDraft', id], id ? () => getPackDraft(id) : null);
  if (!routeId && list.data && list.data.items.length === 0)
    return (
      <div className="px-da-3xl pt-da-2xl">
        <EmptyState
          title="Không có bản nháp gói"
          description="Bản nháp phiên bản gói sẽ hiện ở đây."
        />
      </div>
    );
  return (
    <AsyncRegion state={draft} what="bản nháp gói">
      {(d) => <AuthorBody draft={d} onChanged={draft.reload} />}
    </AsyncRegion>
  );
}

function AuthorBody({ draft, onChanged }: { draft: PackDraft; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<'validate' | 'publish' | null>(null);
  const [error, setError] = useState<string>();
  const failing = draft.conditions.filter((c) => !c.ok);

  const act = async (
    key: 'validate' | 'publish',
    call: () => Promise<unknown>,
    message: string,
  ) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: key === 'validate' ? 'info' : 'success', message });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const publishButton = (
    <Button
      kind="primary"
      loading={busy === 'publish'}
      disabled={busy !== null || failing.length > 0}
      onClick={() =>
        act('publish', () => publishPackVersion(draft.id), `Đã ký số và phát hành ${draft.version}`)
      }
    >
      Phát hành {draft.version}
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Gói ngành', 'Biên soạn']}
        title={
          <span className="flex items-center gap-[10px]">
            {draft.pack_name} {draft.version}
            <StatusDot tone="warning">Bản nháp</StatusDot>
          </span>
        }
        description={draft.summary_text}
        actions={
          <>
            {draft.base_version && (
              <Tooltip content="Màn so sánh hai phiên bản đang được làm.">
                <Button kind="ghost" disabled>
                  So với {draft.base_version}
                </Button>
              </Tooltip>
            )}
            <Button
              loading={busy === 'validate'}
              disabled={busy !== null}
              onClick={() =>
                act(
                  'validate',
                  () => validatePackVersion(draft.id),
                  'Đang kiểm thử trên bộ dữ liệu mẫu',
                )
              }
            >
              Kiểm thử
            </Button>
            {failing.length ? (
              <Tooltip content={`Chưa đạt: ${failing.map((c) => c.text).join('; ')}.`}>
                {publishButton}
              </Tooltip>
            ) : (
              publishButton
            )}
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <ul
          aria-label="Nội dung gói"
          className="flex w-[250px] shrink-0 flex-col border-r border-border pt-da-md max-[1023px]:w-full"
        >
          {draft.sections.map((s) => (
            <li
              key={s.label}
              className="flex items-center gap-da-sm px-da-xl py-[8px] da-body-small"
            >
              <span className="text-foreground">{s.label}</span>
              {s.changed !== null && s.changed > 0 && (
                <span className="ml-auto text-warning da-caption">{s.changed} đổi</span>
              )}
              <span
                className={cn(
                  'w-8 text-right text-muted-foreground da-mono',
                  !s.changed && 'ml-auto',
                )}
              >
                {s.total || ''}
              </span>
            </li>
          ))}
        </ul>
        <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-2xl pb-da-2xl pt-da-xl">
          {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}
          <div className="flex items-baseline gap-da-md">
            <h2 className="text-foreground da-heading-2">Kiểm thử trên bộ dữ liệu mẫu</h2>
            {draft.test && (
              <span className="ml-auto text-muted-foreground da-caption">
                {draft.test.run_text}
              </span>
            )}
          </div>
          {draft.test ? (
            <>
              <dl className="grid grid-cols-4 overflow-hidden rounded-md border border-border max-[1023px]:grid-cols-2">
                {draft.test.kpis.map((k) => (
                  <div
                    key={k.label}
                    className="flex flex-col gap-[2px] border-r border-border px-da-lg py-da-md last:border-r-0"
                  >
                    <dt className="text-muted-foreground da-caption">{k.label}</dt>
                    <dd className={cn('da-body-strong', TONE[k.tone])}>{k.value}</dd>
                  </div>
                ))}
              </dl>
              {draft.test.failures.length > 0 && <FailureTable rows={draft.test.failures} />}
            </>
          ) : (
            <p className="text-muted-foreground da-body-small">Chưa kiểm thử bản nháp này.</p>
          )}
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Điều kiện phát hành</h2>
          <ul className="flex flex-col gap-da-sm da-body-small">
            {draft.conditions.map((c) => (
              <li key={c.text} className="flex items-center gap-da-sm text-foreground">
                <Icon
                  name={c.ok ? 'check' : 'alert'}
                  className={c.ok ? 'text-success' : 'text-warning'}
                  label={c.ok ? 'Đạt' : 'Chưa đạt'}
                />
                {c.text}
              </li>
            ))}
          </ul>
          <h2 className="text-foreground da-heading-3">Sau khi phát hành</h2>
          <p className="text-foreground da-body-small">{draft.after_text}</p>
        </aside>
      </div>
    </div>
  );
}

function FailureTable({ rows }: { rows: { item: string; kind: string; detail: string }[] }) {
  const view = useTableView(rows, {
    search: (f) => `${f.item} ${f.kind} ${f.detail}`,
    filters: [{ id: 'kind', label: 'Loại', value: (f) => f.kind }],
    sorts: [
      { id: 'item', value: (f) => f.item },
      { id: 'kind', value: (f) => f.kind },
      { id: 'detail', value: (f) => f.detail },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm mục chưa đạt…" searchLabel="Tìm mục chưa đạt" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="item" className="w-[98px] px-da-md py-da-sm font-normal">
              Chưa đạt
            </SortableTh>
            <SortableTh view={view} column="kind" className="w-[132px] px-da-md py-da-sm font-normal">
              Loại
            </SortableTh>
            <SortableTh view={view} column="detail" className="px-da-md py-da-sm font-normal">
              Chi tiết
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((f) => (
            <tr key={f.item} className="border-b border-border text-foreground">
              <td className="px-da-md py-[10px]">{f.item}</td>
              <td className="px-da-md py-[10px] text-muted-foreground">{f.kind}</td>
              <td className="px-da-md py-[10px]">{f.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
