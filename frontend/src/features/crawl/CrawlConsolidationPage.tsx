import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { MatrixTable } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  decideEntityMatch,
  getConsolidationRun,
  certifyDatasetVersion,
  type ConsolidationRun,
} from '../../lib/v1/crawl';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P14 · Hợp nhất dữ liệu" (2390:14727, UC-14-05): one consolidation run of a crawled dataset.
 * The steps it went through, the consolidated table and the paragraph that will be embedded — only
 * the table, its claims and that paragraph are, never raw pages (BR-154) — then what still waits on
 * a person: entity links the agent was unsure of, and gaps between sources above the materiality
 * threshold, which already opened a Cần bạn request (BR-155). The run writes the dataset version
 * itself; certifying it is the person's gate (BR-156) and waits on both.
 */
const fmt = (n: number | null) => (n === null ? '—' : n.toFixed(2).replace('.', ','));

const STEP_TONE = { done: 'success', waiting: 'warning', pending: 'neutral' } as const;

export default function CrawlConsolidationPage() {
  const { routeId } = useAppState();
  const run = useV1(
    ['getConsolidationRun', routeId],
    routeId ? () => getConsolidationRun(routeId) : null,
  );
  return (
    <AsyncRegion state={run} what="lượt hợp nhất">
      {(r) => <ConsolidationBody run={r} onChanged={run.reload} />}
    </AsyncRegion>
  );
}

function ConsolidationBody({ run, onChanged }: { run: ConsolidationRun; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const pendingMatches = run.entity_matches.filter((m) => !m.decision);
  const openConflicts = run.conflicts.filter((c) => c.open);
  const published = run.status === 'published';
  const waiting = [
    pendingMatches.length && `${pendingMatches.length} liên kết thực thể`,
    openConflicts.length && `${openConflicts.length} xung đột`,
  ].filter(Boolean);
  const version = run.dataset_version;
  const versionId = run.dataset_version_id;
  const named = version === null ? 'dataset' : `dataset v${version}`;
  const [certified, setCertified] = useState(false);

  const act = async (key: string, call: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const certifyButton = versionId && (
    <Button
      kind="primary"
      loading={busy === 'certify'}
      disabled={busy !== null || waiting.length > 0}
      onClick={() =>
        act(
          'certify',
          async () => {
            await certifyDatasetVersion(versionId);
            setCertified(true);
          },
          `Đã chứng nhận ${named}`,
        )
      }
    >
      Chứng nhận {named}
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Thu thập ngoài', `Hợp nhất · ${run.spec_name}`]}
        title={run.title}
        description={
          certified
            ? `${run.summary_text} · đã chứng nhận ${named}`
            : waiting.length
              ? `${run.summary_text} · chờ ${waiting.join(' và ')} trước khi chứng nhận`
              : `${run.summary_text} · sẵn sàng chứng nhận`
        }
        actions={
          <>
            <Tooltip content="Trình sửa đặc tả hợp nhất đang được làm.">
              <Button disabled>Sửa đặc tả hợp nhất</Button>
            </Tooltip>
            {certifyButton &&
              !certified &&
              (waiting.length ? (
                <Tooltip content={`Còn ${waiting.join(' và ')} chờ quyết định.`}>
                  {certifyButton}
                </Tooltip>
              ) : (
                certifyButton
              ))}
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {published && (
          <Notice tone="success">
            Lượt hợp nhất đã ghi {named}
            {certified ? ' và bạn đã chứng nhận.' : '; chờ chứng nhận.'}
          </Notice>
        )}
        {run.status === 'failed' && <Notice tone="error">Lượt hợp nhất thất bại.</Notice>}
        {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}
        {run.steps.length > 0 && (
          <ol aria-label="Các bước hợp nhất" className="flex gap-da-sm max-[1023px]:flex-wrap">
            {run.steps.map((s) => (
              <li
                key={s.label}
                className="min-w-[120px] flex-1 rounded-sm border border-border bg-card px-da-md py-[5px] da-caption"
              >
                <StatusDot tone={STEP_TONE[s.state]}>{stepLabel(run, s.label, s.state)}</StatusDot>
              </li>
            ))}
          </ol>
        )}
        <div className="flex items-start gap-da-xl max-[1279px]:flex-col">
          {run.table && (
            <Card title={run.table.title} className="min-w-0 flex-1">
              <MatrixTable
                columns={run.table.columns}
                rows={run.table.rows}
                highlightRow={run.table.highlighted_row}
                className="w-full table-fixed border-collapse da-body-small"
                searchLabel="Tìm trong bảng hợp nhất"
              />
              {run.table.note && (
                <p className="text-muted-foreground da-caption">{run.table.note}</p>
              )}
            </Card>
          )}
          <div className="flex w-[476px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
            {run.summary_paragraph && (
              <Card title="Đoạn tóm tắt sẽ nhúng">
                <p className="text-foreground da-body-small">{run.summary_paragraph.text}</p>
                <p className="text-muted-foreground da-caption">
                  {run.summary_paragraph.provenance}
                </p>
              </Card>
            )}
            <Card title={`Nhận diện thực thể chờ duyệt · ${pendingMatches.length}`}>
              {run.entity_matches.length === 0 ? (
                <p className="text-muted-foreground da-body-small">Không có liên kết chờ.</p>
              ) : (
                <ul className="flex flex-col gap-da-md">
                  {run.entity_matches.map((m) => (
                    <li
                      key={m.id}
                      aria-label={`Liên kết ${m.alias}`}
                      className="flex items-center gap-da-md"
                    >
                      <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
                        <p className="text-foreground da-body-small">
                          {m.alias} ↔ {m.canonical}
                        </p>
                        <p className="text-muted-foreground da-caption">
                          điểm {fmt(m.score)} · {m.reason}
                        </p>
                      </div>
                      {m.decision ? (
                        <span className="shrink-0 text-success da-caption">
                          {m.decision === 'merge' ? 'Đã gộp' : 'Đã tách'}
                        </span>
                      ) : (
                        <div className="flex shrink-0 gap-da-sm">
                          <Button
                            size="sm"
                            disabled={busy !== null || certified}
                            loading={busy === `${m.id}:split`}
                            onClick={() =>
                              act(
                                `${m.id}:split`,
                                () => decideEntityMatch(m.id, 'split'),
                                `Đã tách ${m.alias}`,
                              )
                            }
                          >
                            Tách
                          </Button>
                          <Button
                            size="sm"
                            kind="primary"
                            disabled={busy !== null || certified}
                            loading={busy === `${m.id}:merge`}
                            onClick={() =>
                              act(
                                `${m.id}:merge`,
                                () => decideEntityMatch(m.id, 'merge'),
                                `Đã gộp ${m.alias} vào ${m.canonical}`,
                              )
                            }
                          >
                            Gộp
                          </Button>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
        <Card title={`Xung đột giữa nguồn · ${openConflicts.length}`}>
          {run.conflicts.length === 0 ? (
            <p className="text-muted-foreground da-body-small">Các nguồn khớp nhau.</p>
          ) : (
            <ul className="flex flex-col gap-da-md">
              {run.conflicts.map((c) => (
                <li key={c.text} className="flex items-start gap-da-sm da-body-small">
                  <span
                    aria-hidden
                    className={cn(
                      'mt-[7px] size-2 shrink-0 rounded-full',
                      c.open ? 'bg-warning' : 'bg-success',
                    )}
                  />
                  <span className="text-foreground">{c.text}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-muted-foreground da-caption">
            Chỉ bảng tổng hợp, khẳng định và đoạn tóm tắt được nhúng vector; bản ghi thô không nhúng
            (BR-154). Mỗi giá trị giữ nguồn và liên kết tới trang gốc (BR-155).
          </p>
        </Card>
      </div>
    </div>
  );
}

// The design counts what waits in the step chip itself ("Thực thể · 2 chờ", "Đối chiếu · 1").
function stepLabel(run: ConsolidationRun, label: string, state: string) {
  if (state !== 'waiting') return label;
  if (label === 'Thực thể') {
    const n = run.entity_matches.filter((m) => !m.decision).length;
    return n ? `${label} · ${n} chờ` : label;
  }
  if (label === 'Đối chiếu') {
    const n = run.conflicts.filter((c) => c.open).length;
    return n ? `${label} · ${n}` : label;
  }
  return label;
}

function Card({
  title,
  className,
  children,
}: {
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        'flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl',
        className,
      )}
    >
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}
