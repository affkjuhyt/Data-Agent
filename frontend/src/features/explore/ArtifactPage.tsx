import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import {
  getArtifact,
  restoreArtifactVersion,
  saveArtifact,
  updateArtifactUsages,
  watchArtifact,
  type Artifact,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';

/*
 * "v3.1 · Chi tiết · Artifact toàn màn" (2074:4442, UC-01-01): one chart or table produced in
 * an answer or a notebook, opened full screen at /analysis/artifact/<id>. Main column (24px
 * sides): the session breadcrumb, title with version and "Đã kiểm tra số", the provenance line,
 * Lưu · Theo dõi · Xuất · Chia sẻ; Biểu đồ / Bảng / SQL; the horizontal bars (chart-3 over the
 * watch threshold, chart-1 for a decrease) and the table. The 380px side column holds the
 * version history with restore, what changed from the previous version, where the artifact is
 * used, and a warning when a snapshot still shows an older version.
 * /v1/artifacts is a PROPOSAL (lib/v1/explore.ts).
 */
type Mode = 'chart' | 'table' | 'sql';

export default function ArtifactPage() {
  const { routeId } = useAppState();
  const artifact = useV1(routeId ? () => getArtifact(routeId) : null, [routeId]);

  if (!routeId)
    return (
      <div className="p-da-2xl">
        <EmptyState
          icon="chart"
          title="Chưa chọn kết quả nào"
          description="Mở một biểu đồ hoặc bảng từ câu trả lời hay phiên làm việc để xem toàn màn."
        />
      </div>
    );

  return (
    <AsyncRegion state={artifact} what="kết quả" skeletonRows={6}>
      {(a) => <ArtifactView artifact={a} />}
    </AsyncRegion>
  );
}

function ArtifactView({ artifact: initial }: { artifact: Artifact }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [artifact, setArtifact] = useState(initial);
  const [mode, setMode] = useState<Mode>('chart');
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (key: string, run: () => Promise<Artifact>, done: string) => {
    setBusy(key);
    try {
      setArtifact(await run());
      toast({ tone: 'success', message: done });
    } catch (e) {
      toast({ tone: 'error', message: `Không thực hiện được: ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  const exportCsv = () => {
    const lines = [
      artifact.table.columns.map(csvCell).join(','),
      ...artifact.table.rows.map((r) => r.cells.map(csvCell).join(',')),
    ];
    const blob = new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${artifact.title}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast({
        tone: 'success',
        message: 'Đã sao chép liên kết. Người nhận cần quyền trên Data Space này.',
      });
    } catch {
      toast({ tone: 'error', message: 'Không sao chép được liên kết.' });
    }
  };

  const stale = artifact.used_in.filter(
    (u) => u.pinned_version !== null && u.pinned_version < artifact.version,
  );

  return (
    <div className="flex h-full min-h-0">
      <main className="flex min-w-0 flex-1 flex-col gap-da-lg overflow-y-auto px-da-2xl py-da-xl">
        <header className="flex items-center gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col">
            <nav aria-label="Vị trí" className="text-muted-foreground da-caption">
              {artifact.session ? (
                <button
                  type="button"
                  onClick={() => navigate('analysis', 'SESSIONS', artifact.session!.id)}
                  className="hover:text-foreground hover:underline"
                >
                  {artifact.session.title}
                </button>
              ) : (
                'Câu trả lời'
              )}
              {' › Artifact'}
            </nav>
            <div className="flex flex-wrap items-center gap-da-md">
              <h1 className="text-foreground da-heading-1">{artifact.title}</h1>
              <StatusDot tone="primary">v{artifact.version}</StatusDot>
              {artifact.checked && <StatusDot tone="success">Đã kiểm tra số</StatusDot>}
            </div>
            <p className="text-muted-foreground da-caption">{artifact.meta_text}</p>
          </div>
          <Button
            kind="ghost"
            icon="bookmark"
            loading={busy === 'save'}
            disabled={artifact.saved}
            onClick={() =>
              void act('save', () => saveArtifact(artifact.id), 'Đã lưu vào mục của bạn')
            }
          >
            {artifact.saved ? 'Đã lưu' : 'Lưu'}
          </Button>
          <Button
            kind="ghost"
            icon="bell"
            loading={busy === 'watch'}
            disabled={artifact.watching}
            onClick={() =>
              void act('watch', () => watchArtifact(artifact.id), 'Sẽ báo bạn khi số này thay đổi')
            }
          >
            {artifact.watching ? 'Đang theo dõi' : 'Theo dõi'}
          </Button>
          <Button onClick={exportCsv}>Xuất</Button>
          <Button onClick={() => void share()}>Chia sẻ</Button>
        </header>

        <Segmented<Mode>
          label="Chế độ"
          value={mode}
          onChange={setMode}
          items={[
            { value: 'chart', label: 'Biểu đồ' },
            { value: 'table', label: 'Bảng' },
            { value: 'sql', label: 'SQL' },
          ]}
        />

        {mode === 'chart' && <Bars artifact={artifact} />}
        {mode !== 'sql' && <Table artifact={artifact} />}
        {mode === 'sql' && (
          <pre className="overflow-x-auto rounded-lg border border-border bg-subtle px-da-xl py-da-lg text-foreground da-mono">
            {artifact.sql}
          </pre>
        )}
      </main>

      <aside
        aria-label="Phiên bản và nơi dùng"
        className="flex w-[380px] shrink-0 flex-col gap-da-lg overflow-y-auto border-l border-border bg-card p-da-xl max-[1279px]:hidden"
      >
        <h2 className="uppercase text-muted-foreground da-overline">Lịch sử phiên bản</h2>
        <ol className="flex flex-col gap-da-lg">
          {artifact.versions.map((v) => {
            const current = v.version === artifact.version;
            return (
              <li
                key={v.version}
                aria-current={current || undefined}
                className={cn(
                  'flex items-start gap-da-md rounded-md px-da-md py-da-sm',
                  current && 'border border-border',
                )}
              >
                <span className="text-primary da-mono">v{v.version}</span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-foreground da-body-small">{v.summary}</span>
                  <span className="text-muted-foreground da-caption">{v.author_text}</span>
                </span>
                {!current && (
                  <button
                    type="button"
                    aria-label={`Khôi phục v${v.version}`}
                    title={`Khôi phục v${v.version}`}
                    disabled={busy !== null}
                    onClick={() =>
                      void act(
                        `restore-${v.version}`,
                        () => restoreArtifactVersion(artifact.id, v.version),
                        `Đã khôi phục v${v.version} thành phiên bản mới`,
                      )
                    }
                    className="inline-flex text-muted-foreground hover:text-foreground"
                  >
                    <Icon name="rollback" />
                  </button>
                )}
              </li>
            );
          })}
        </ol>
        {artifact.compare && (
          <div className="flex flex-col gap-da-xs rounded-md border border-border px-da-lg py-da-md">
            <span className="text-muted-foreground da-mono">{artifact.compare.label}</span>
            <p className="text-foreground da-body-small">{artifact.compare.text}</p>
          </div>
        )}

        {artifact.used_in.length > 0 && (
          <>
            <h2 className="uppercase text-muted-foreground da-overline">Đang dùng ở</h2>
            <ul className="flex flex-col gap-da-sm">
              {artifact.used_in.map((u) => (
                <li key={u.id} className="text-foreground da-body-small">
                  · {u.text}
                </li>
              ))}
            </ul>
          </>
        )}
        {stale.length > 0 && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              void act(
                'usages',
                () =>
                  updateArtifactUsages(
                    artifact.id,
                    stale.map((u) => u.id),
                  ),
                `Đã cập nhật lên v${artifact.version}`,
              )
            }
            className="flex items-start gap-da-md rounded-md bg-warning-subtle px-da-md py-da-sm text-left text-foreground da-caption"
          >
            <Icon name="alert" className="text-warning" />
            <span>
              {stale
                .map((u) => `${u.text.split(' · ')[0]} đang dùng v${u.pinned_version} đã cũ`)
                .join('; ')}
              . Cập nhật lên v{artifact.version}?
            </span>
          </button>
        )}
      </aside>
    </div>
  );
}

const BAR_TONE = { above: 'bg-chart-3', normal: 'bg-chart-6', down: 'bg-chart-1' } as const;

function Bars({ artifact }: { artifact: Artifact }) {
  const max = Math.max(...artifact.chart.bars.map((b) => Math.abs(b.value))) || 1;
  return (
    <section
      aria-label="Biểu đồ"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-2xl py-da-xl"
    >
      <ul className="flex flex-col gap-da-md">
        {artifact.chart.bars.map((b) => (
          <li key={b.label} className="flex items-center gap-da-md">
            <span className="w-[150px] shrink-0 truncate text-foreground da-body-small">
              {b.label}
            </span>
            <span className="flex min-w-0 flex-1 items-center gap-da-md">
              <span
                className={cn('h-[22px] rounded-sm', BAR_TONE[b.tone])}
                style={{ width: `${Math.max(3, (Math.abs(b.value) / max) * 85)}%` }}
                aria-hidden
              />
              <span className={cn('da-mono', b.value > 0 ? 'text-error' : 'text-success')}>
                {b.value_text}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground da-caption">{artifact.chart.caption}</p>
    </section>
  );
}

function Table({ artifact }: { artifact: Artifact }) {
  const { columns, rows, change_column } = artifact.table;
  return (
    <table className="w-full border-separate border-spacing-0 overflow-hidden rounded-lg border border-border bg-card">
      <thead>
        <tr className="bg-subtle text-muted-foreground da-label">
          {columns.map((c, i) => (
            <th
              key={c}
              className={cn('px-da-lg py-da-sm font-medium', i === 0 ? 'text-left' : 'text-right')}
            >
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.cells[0]}>
            {r.cells.map((cell, i) => (
              <td
                key={columns[i] ?? i}
                className={cn(
                  'border-t border-border px-da-lg py-da-sm text-foreground',
                  i === 0 ? 'da-body-small' : 'text-right da-mono',
                )}
              >
                {i === change_column ? (
                  <StatusDot
                    tone={r.trend === 'up' ? 'error' : r.trend === 'down' ? 'success' : 'idle'}
                  >
                    {cell}
                  </StatusDot>
                ) : (
                  cell
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
