import { useEffect, useState, type FormEvent } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  addTileComment,
  createSessionReport,
  decideTile,
  getTileThread,
  getWorkspace,
  listSessionReports,
  refreshWorkspace,
  type Author,
  type WorkspaceDetail,
  type WorkspaceTile,
} from '../../lib/v1/sessions';
import { useV1 } from '../../lib/v1/useV1';
import { SessionToAgentDialog } from '../skill-authoring/SessionToAgentDialog';
import { ShareDialog } from './ShareDialog';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P02 · Phiên · Notebook" (2297:18612, UC-02-01, 02, 04, 05, 06): a bordered header with who is
 * working and the session parameters; a band when a source refreshed and tiles must re-run
 * (UC-02-02); the tiles, numbered, an agent's proposal drawn dashed until a person accepts it
 * (UC-02-06); on the right, on --background, the comments and tasks of the chosen tile (UC-02-05).
 * "Dạng hội thoại" (the same session as a conversation) is a later screen; UC-02-04 (turn into a
 * scheduled report) lives in P08.
 */
const PENDING = 'Màn này đang được làm.';

const KIND_LABEL: Record<WorkspaceTile['kind'], string> = {
  text: 'Văn bản',
  sql: 'SQL',
  chart: 'Biểu đồ',
  skill: 'Kỹ năng',
};

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

const WEEKDAY = ['Chủ Nhật', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy'];
const dueText = (iso: string) => {
  const d = new Date(iso);
  const day = `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
  return `hạn ${WEEKDAY[d.getDay()]} ${day}`;
};

/** "Đỗ Hải Yến, Phạm Đức Long và Agent Phân tích". */
const joinNames = (authors: Author[]) => {
  const names = authors.map((a) => a.name);
  return names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} và ${names.at(-1)}`;
};

/** "Ô 2 và 4", "Ô 2, 3 và 4". */
const tileList = (tiles: WorkspaceTile[]) => {
  const seqs = tiles.map((t) => String(t.seq));
  return seqs.length <= 1 ? seqs.join('') : `${seqs.slice(0, -1).join(', ')} và ${seqs.at(-1)}`;
};

export function NotebookPage({ workspaceId }: { workspaceId: string }) {
  const workspace = useV1(() => getWorkspace(workspaceId), [workspaceId]);
  // A workspace in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(workspace.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={workspace} what="phiên làm việc">
      {(ws) => <Notebook ws={ws} onChanged={workspace.reload} />}
    </AsyncRegion>
  );
}

function Notebook({ ws: loaded, onChanged }: { ws: WorkspaceDetail; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  // Actions answer with the whole workspace, so it is kept here rather than reloaded.
  const [ws, setWs] = useState(loaded);
  useEffect(() => setWs(loaded), [loaded]);
  const [selected, setSelected] = useState<string | null>(
    () => (loaded.tiles.find((t) => t.comment_count > 0) ?? loaded.tiles[0])?.id ?? null,
  );
  const [sharing, setSharing] = useState(false);
  const [toAgent, setToAgent] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();

  const stale = ws.tiles.filter((t) => t.state === 'stale');
  const selectedTile = ws.tiles.find((t) => t.id === selected) ?? null;

  const run = async (key: string, action: () => Promise<WorkspaceDetail>, done?: string) => {
    setBusy(key);
    setError(undefined);
    try {
      setWs(await action());
      if (done) toast({ tone: 'success', message: done });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const openReport = async () => {
    setBusy('report');
    setError(undefined);
    try {
      // An unpublished report of this session is reviewed, not drafted again; a list that cannot
      // be read is an error, not a reason to draft a second one.
      const drafts = await listSessionReports(ws.id);
      const existing = drafts.items.find((r) => r.status !== 'published');
      const report = existing ?? (await createSessionReport(ws.id, ws.title));
      navigate('analysis', 'REPORT', report.id);
    } catch (e) {
      setError(`Không tạo được báo cáo: ${(e as Error).message}`);
      setBusy(null);
    }
  };

  const params = ws.params.map((p) => `${p.name} = ${p.value}`).join(', ');

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Phiên', ws.title]}
        title={ws.title}
        description={[
          ws.working.length > 0 ? `${joinNames(ws.working)} đang làm` : null,
          params ? `tham số phiên: ${params}` : null,
        ]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <>
            <Tooltip content={PENDING}>
              <Button kind="ghost" disabled>
                Dạng hội thoại
              </Button>
            </Tooltip>
            {/* "v3.1 · Chi tiết · Biến phiên thành agent" (2070:3837): a session that repeats becomes an agent. */}
            <Button kind="ghost" icon="agent" onClick={() => setToAgent(true)}>
              Biến thành agent
            </Button>
            <Button onClick={() => setSharing(true)}>Chia sẻ</Button>
            <Button kind="primary" loading={busy === 'report'} onClick={openReport}>
              Tạo báo cáo
            </Button>
          </>
        }
      />

      {stale.length > 0 && (
        <div className="flex items-center gap-[10px] border-b border-border bg-background px-da-3xl py-da-md">
          <StatusDot tone="warning" />
          <p className="min-w-0 flex-1 text-foreground da-body-small">
            {ws.refresh &&
              `Dữ liệu ${ws.refresh.source_name} cập nhật lúc ${hhmm(ws.refresh.refreshed_at)}. `}
            Ô {tileList(stale)} dùng số cũ và cần chạy lại.
          </p>
          <button
            type="button"
            disabled={busy === 'refresh'}
            onClick={() =>
              run('refresh', () => refreshWorkspace(ws.id), `Đang chạy lại ${stale.length} ô`)
            }
            className="shrink-0 text-primary da-body-strong hover:underline disabled:text-muted-foreground"
          >
            Chạy lại {stale.length} ô
          </button>
        </div>
      )}

      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] px-[40px] py-da-xl">
          {ws.tiles.length === 0 ? (
            <EmptyState
              icon="layers"
              title="Phiên chưa có ô nào"
              description="Hỏi một câu ở Hỏi đáp, agent sẽ thêm ô đầu tiên vào phiên này."
              action={<Button onClick={() => navigate('analysis', 'ASK')}>Hỏi đáp</Button>}
            />
          ) : (
            ws.tiles.map((tile) => (
              <TileRow
                key={tile.id}
                tile={tile}
                selected={tile.id === selected}
                busy={busy === tile.id}
                onSelect={() => setSelected(tile.id)}
                onAccept={() =>
                  run(tile.id, () => decideTile(ws.id, tile.id, 'accept'), `Đã thêm ô ${tile.seq}`)
                }
                onDismiss={() => run(tile.id, () => decideTile(ws.id, tile.id, 'dismiss'))}
              />
            ))
          )}
        </div>

        {selectedTile && (
          <CommentPanel
            workspaceId={ws.id}
            tile={selectedTile}
            onCommented={() => {
              setWs((prev) => ({
                ...prev,
                tiles: prev.tiles.map((t) =>
                  t.id === selectedTile.id ? { ...t, comment_count: t.comment_count + 1 } : t,
                ),
              }));
            }}
          />
        )}
      </div>

      <SessionToAgentDialog sessionId={ws.id} open={toAgent} onClose={() => setToAgent(false)} />
      <ShareDialog
        open={sharing}
        workspace={ws}
        onClose={() => setSharing(false)}
        onShared={() => {
          setSharing(false);
          onChanged();
        }}
      />
    </div>
  );
}

function TileRow({
  tile,
  selected,
  busy,
  onSelect,
  onAccept,
  onDismiss,
}: {
  tile: WorkspaceTile;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const proposed = tile.state === 'proposed';
  const activity = [
    tile.comment_count > 0 ? `${tile.comment_count} bình luận` : null,
    tile.task_count > 0 ? `${tile.task_count} việc được giao` : null,
  ].filter(Boolean);

  return (
    <article
      aria-label={`Ô ${tile.seq} · ${tile.title || KIND_LABEL[tile.kind]}`}
      className="flex w-full items-start gap-da-lg"
    >
      <span className="w-[28px] shrink-0 text-muted-foreground da-mono">[{tile.seq}]</span>
      <div
        className={cn(
          'flex min-w-0 flex-1 flex-col gap-da-sm rounded-md border px-[14px] py-[10px]',
          proposed
            ? 'border-dashed border-primary'
            : selected
              ? 'border-border-strong'
              : 'border-border',
        )}
      >
        <div className="flex items-center gap-da-md">
          <span className="shrink-0 text-muted-foreground da-caption">
            {proposed ? 'Đề xuất của agent' : KIND_LABEL[tile.kind]}
          </span>
          <button
            type="button"
            onClick={onSelect}
            aria-pressed={selected}
            className="min-w-0 flex-1 truncate text-left text-foreground da-body-strong hover:underline"
          >
            {tile.title || KIND_LABEL[tile.kind]}
          </button>
          {tile.state === 'stale' && <StatusDot tone="warning">Cần chạy lại</StatusDot>}
          {tile.author.name && (
            <span className="shrink-0 text-muted-foreground da-caption">{tile.author.name}</span>
          )}
        </div>
        <p
          className={cn(
            tile.kind === 'sql' && !proposed
              ? 'text-muted-foreground da-mono'
              : 'text-foreground da-body-small',
          )}
        >
          {tile.body}
        </p>
        {activity.length > 0 && (
          <button type="button" onClick={onSelect} className="self-start hover:underline">
            <StatusDot tone="primary" colorText className="da-caption">
              {activity.join(' · ')}
            </StatusDot>
          </button>
        )}
        {proposed && (
          <div className="flex gap-da-md">
            <Button loading={busy} onClick={onAccept}>
              Thêm ô
            </Button>
            <Button kind="ghost" disabled={busy} onClick={onDismiss}>
              Bỏ
            </Button>
          </div>
        )}
      </div>
    </article>
  );
}

function CommentPanel({
  workspaceId,
  tile,
  onCommented,
}: {
  workspaceId: string;
  tile: WorkspaceTile;
  onCommented: () => void;
}) {
  const thread = useV1(() => getTileThread(workspaceId, tile.id), [workspaceId, tile.id]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) return;
    setSending(true);
    setError(undefined);
    try {
      await addTileComment(tile.id, draft);
      setDraft('');
      thread.reload();
      onCommented();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <aside
      aria-label="Bình luận"
      className="flex w-[340px] shrink-0 flex-col gap-da-lg border-l border-border bg-background px-[20px] py-da-xl"
    >
      <h2 className="text-foreground da-heading-3">
        Ô {tile.seq} · {tile.title || KIND_LABEL[tile.kind]}
      </h2>
      <AsyncRegion
        state={thread}
        what="bình luận"
        isEmpty={(t) => t.items.length === 0 && t.tasks.length === 0}
        empty={
          <p className="text-muted-foreground da-body-small">
            Chưa có bình luận. Gõ @ để nhắc người hoặc agent.
          </p>
        }
      >
        {(t) => (
          <>
            {t.items.map((c) => (
              <div key={c.id} className="flex flex-col gap-da-xs">
                <div className="flex items-center gap-da-sm">
                  <span className="text-foreground da-body-strong">{c.author.name}</span>
                  <span className="text-muted-foreground da-caption">{hhmm(c.created_at)}</span>
                </div>
                <p className="text-foreground da-body-small">{c.body}</p>
              </div>
            ))}
            {t.tasks.map((task) => (
              <div
                key={task.id}
                className="flex flex-col gap-da-sm rounded-md border border-border bg-card px-da-lg py-[10px]"
              >
                <span className="text-muted-foreground da-caption">Việc được giao</span>
                <span className="text-foreground da-body-strong">{task.title}</span>
                <span className="text-muted-foreground da-caption">
                  {task.assignee_name} · {dueText(task.due_at)}
                </span>
              </div>
            ))}
          </>
        )}
      </AsyncRegion>
      <form onSubmit={submit} className="mt-da-xl flex flex-col gap-da-xs">
        <Input
          aria-label="Bình luận"
          placeholder="Bình luận, @ để nhắc người hoặc agent…"
          value={draft}
          disabled={sending}
          onChange={(e) => setDraft(e.target.value)}
          error={error}
        />
      </form>
    </aside>
  );
}
