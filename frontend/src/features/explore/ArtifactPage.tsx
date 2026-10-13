import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import {
  getArtifact,
  getQuerySql,
  saveArtifactToSession,
  type Artifact,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';
import { SaveToSessionDialog } from './SaveToSessionDialog';

/*
 * "v3.1 · Chi tiết · Artifact toàn màn" (2074:4442, UC-01-01): one answer opened full screen at
 * /analysis/artifact/<answer id> (GET /v1/answers/{id}). The question as title with "Đã kiểm tra
 * số", the provenance line, Lưu · Theo dõi · Xuất · Chia sẻ; Biểu đồ / Bảng / SQL; the
 * horizontal bars of the figures (chart-1 for a decrease) and the table. "Lưu" keeps it as a
 * tile of a session, "Theo dõi" opens Tạo theo dõi (POST /v1/monitors needs a rule), SQL is the
 * run's (GET /v1/query-runs/{id}/sql). The frame's side column (version history, restore, where
 * it is used) is not drawn: no use case versions an artifact, a report figure follows its tile.
 */
type Mode = 'chart' | 'table' | 'sql';

export default function ArtifactPage() {
  const { routeId } = useAppState();
  const artifact = useV1(routeId ? () => getArtifact(routeId) : null, [routeId]);

  // A artifact in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(artifact.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
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

function ArtifactView({ artifact }: { artifact: Artifact }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>(artifact.chart ? 'chart' : 'table');
  const [saving, setSaving] = useState(false);
  const runId = artifact.query_run_id;
  const sql = useV1(mode === 'sql' && runId ? () => getQuerySql(runId) : null, [mode, runId]);

  const exportCsv = () => {
    const lines = [
      artifact.table.columns.map(csvCell).join(','),
      ...artifact.table.rows.map((r) => r.map(csvCell).join(',')),
    ];
    const blob = new Blob([`\uFEFF${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
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

  const modes: { value: Mode; label: string }[] = [
    ...(artifact.chart ? [{ value: 'chart' as const, label: 'Biểu đồ' }] : []),
    { value: 'table', label: 'Bảng' },
    ...(runId ? [{ value: 'sql' as const, label: 'SQL' }] : []),
  ];

  return (
    <div className="flex h-full min-h-0">
      <main className="flex min-w-0 flex-1 flex-col gap-da-lg overflow-y-auto px-da-2xl py-da-xl">
        <header className="flex items-center gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col">
            <nav aria-label="Vị trí" className="text-muted-foreground da-caption">
              Câu trả lời › Artifact
            </nav>
            <div className="flex flex-wrap items-center gap-da-md">
              <h1 className="text-foreground da-heading-1">{artifact.title}</h1>
              {artifact.checked && <StatusDot tone="success">Đã kiểm tra số</StatusDot>}
            </div>
            {artifact.meta_text && (
              <p className="text-muted-foreground da-caption">{artifact.meta_text}</p>
            )}
          </div>
          <Button kind="ghost" icon="bookmark" onClick={() => setSaving(true)}>
            Lưu
          </Button>
          <Button kind="ghost" icon="bell" onClick={() => navigate('analysis', 'WATCH_NEW')}>
            Theo dõi
          </Button>
          <Button onClick={exportCsv}>Xuất</Button>
          <Button onClick={() => void share()}>Chia sẻ</Button>
        </header>

        {artifact.text && <p className="text-foreground da-body">{artifact.text}</p>}

        {modes.length > 1 && (
          <Segmented<Mode> label="Chế độ" value={mode} onChange={setMode} items={modes} />
        )}

        {mode === 'chart' && artifact.chart && <Bars bars={artifact.chart.bars} />}
        {mode !== 'sql' && <Table artifact={artifact} />}
        {mode === 'sql' && (
          <AsyncRegion state={sql} what="SQL" skeletonRows={3}>
            {(q) => (
              <pre className="overflow-x-auto rounded-lg border border-border bg-subtle px-da-xl py-da-lg text-foreground da-mono">
                {q.sql_executed || q.sql_generated || '—'}
              </pre>
            )}
          </AsyncRegion>
        )}
      </main>

      {saving && (
        <SaveToSessionDialog
          onClose={() => setSaving(false)}
          onSave={(workspaceId) => saveArtifactToSession(workspaceId, artifact)}
          onSaved={(workspaceId, title) => {
            setSaving(false);
            toast({
              tone: 'success',
              message: `Đã lưu vào ${title}`,
              action: {
                label: 'Mở phiên',
                onClick: () => navigate('analysis', 'SESSIONS', workspaceId),
              },
            });
          }}
        />
      )}
    </div>
  );
}

const BAR_TONE = { normal: 'bg-chart-6', down: 'bg-chart-1' } as const;

function Bars({ bars }: { bars: NonNullable<Artifact['chart']>['bars'] }) {
  const max = Math.max(...bars.map((b) => Math.abs(b.value))) || 1;
  return (
    <section
      aria-label="Biểu đồ"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-2xl py-da-xl"
    >
      <ul className="flex flex-col gap-da-md">
        {bars.map((b) => (
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
              <span className="text-foreground da-mono">{b.value_text}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Table({ artifact }: { artifact: Artifact }) {
  const { columns, rows } = artifact.table;
  if (rows.length === 0)
    return <p className="text-muted-foreground da-body-small">Câu trả lời này không có số.</p>;
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
        {rows.map((r, n) => (
          <tr key={n}>
            {r.map((cell, i) => (
              <td
                key={columns[i] ?? i}
                className={cn(
                  'border-t border-border px-da-lg py-da-sm text-foreground',
                  i === 0 ? 'da-body-small' : 'text-right da-mono',
                )}
              >
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
