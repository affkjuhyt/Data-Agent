import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { EmptyState } from '../../design-system/EmptyState';
import { cn } from '../../lib/utils';
import { getReportRun, listMyDigests, type ReportRun } from '../../lib/v1/reports';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P08 · Bản tin · Đọc" (2272:16218, UC-08-04): the reports and digests sent to the reader, newest
 * first and unread ones dotted, beside the one being read — what changed, the key figures, why, and
 * what waits on a decision (open ones lead to Cần bạn). Opened without an id, the newest is shown.
 */
const DELTA = { up_bad: 'text-error', up_good: 'text-success', neutral: 'text-muted-foreground' };

export default function DigestPage() {
  const { routeId, navigate } = useAppState();
  const list = useV1(listMyDigests, []);
  const id = routeId || list.data?.items[0]?.id;
  const run = useV1(id ? () => getReportRun(id) : null, [id]);
  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <nav
        aria-label="Bản tin"
        className="flex w-[260px] shrink-0 flex-col border-r border-border max-[1023px]:w-full max-[1023px]:border-r-0 max-[1023px]:border-b"
      >
        <h2 className="px-da-xl pb-da-md pt-da-xl text-foreground da-heading-3">Bản tin</h2>
        <AsyncRegion state={list} what="bản tin">
          {(page) => (
            <ul className="flex flex-col">
              {page.items.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    aria-current={d.id === id ? 'page' : undefined}
                    onClick={() => navigate('analysis', 'DIGEST', d.id)}
                    className={cn(
                      'flex w-full flex-col gap-[2px] px-da-xl py-da-md text-left',
                      d.id === id ? 'bg-subtle' : 'hover:bg-subtle',
                    )}
                  >
                    <span className="flex items-center gap-da-sm text-foreground da-body-small">
                      {d.unread && (
                        <span
                          aria-label="chưa đọc"
                          className="size-[7px] shrink-0 rounded-full bg-primary"
                        />
                      )}
                      {d.title}
                    </span>
                    <span className="text-muted-foreground da-caption">{d.meta_text}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </AsyncRegion>
      </nav>
      <div className="min-w-0 flex-1 px-da-3xl pb-da-2xl pt-da-2xl">
        {list.data && list.data.items.length === 0 ? (
          <EmptyState
            title="Chưa có bản tin"
            description="Bản tin và báo cáo gửi cho bạn sẽ hiện ở đây."
          />
        ) : (
          <AsyncRegion state={run} what="bản tin">
            {(r) => <Reading run={r} onOpenDecision={() => navigate('needs-you')} />}
          </AsyncRegion>
        )}
      </div>
    </div>
  );
}

function Reading({ run, onOpenDecision }: { run: ReportRun; onOpenDecision: () => void }) {
  return (
    <article className="mx-auto flex w-[680px] max-w-full flex-col gap-da-lg">
      <header className="flex flex-col gap-da-md">
        <h1 className="text-foreground da-display">{run.title}</h1>
        <p className="text-muted-foreground da-body-small">{run.meta_text}</p>
      </header>
      <h2 className="pt-da-sm text-foreground da-heading-2">Điều gì thay đổi</h2>
      <p className="text-foreground da-reading">{run.changed}</p>
      <dl className="grid grid-cols-3 overflow-hidden rounded-md border border-border max-[767px]:grid-cols-1">
        {run.kpis.map((k) => (
          <div
            key={k.label}
            className="flex flex-col gap-[2px] border-r border-border px-da-lg py-da-md last:border-r-0"
          >
            <dt className="text-muted-foreground da-caption">{k.label}</dt>
            <dd className="text-foreground da-metric">{k.value}</dd>
            <dd className={cn('da-caption', DELTA[k.tone])}>{k.delta}</dd>
          </div>
        ))}
      </dl>
      <h2 className="pt-da-sm text-foreground da-heading-2">Vì sao</h2>
      <p className="text-foreground da-reading">{run.why}</p>
      <h2 className="pt-da-sm text-foreground da-heading-2">Cần quyết định</h2>
      <ul className="flex flex-col border-t border-border">
        {run.decisions.map((d) => (
          <li key={d.text} className="flex items-center gap-da-md border-b border-border py-[10px]">
            {d.hitl_id ? (
              <button
                type="button"
                onClick={onOpenDecision}
                className="min-w-0 flex-1 text-left text-foreground hover:underline da-body"
              >
                {d.text}
              </button>
            ) : (
              <span className="min-w-0 flex-1 text-foreground da-body">{d.text}</span>
            )}
            <span className="shrink-0 text-muted-foreground da-caption">{d.who_text}</span>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground da-caption">{run.footer_text}</p>
    </article>
  );
}
