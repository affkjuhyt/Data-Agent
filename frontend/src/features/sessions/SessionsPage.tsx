import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatElapsed } from '../../lib/formatVN';
import { listWorkspaces } from '../../lib/v1/api';
import { createBlankWorkspace } from '../../lib/v1/sessions';
import type { Workspace } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { NotebookPage } from './NotebookPage';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * Phân tích › Phiên làm việc (P02). /analysis/sessions lists the viewer's sessions, the way
 * "v3.1 · Phân tích" (2155:16027) lists the recent ones; /analysis/sessions/<workspace id> opens
 * one as a notebook ("P02 · Phiên · Notebook", 2297:18612). Its report under review is its own
 * page, /analysis/report/<report id> (ReportReviewPage), so Cần bạn can link to it.
 *
 * "Hỏi mới" opens Ask (conversation). "Notebook mới" POSTs a blank workspace — not the same thing.
 */
const FROM_LABEL: Record<Workspace['created_from'], string> = {
  blank: 'Notebook',
  conversation: 'Hội thoại',
  template: 'Từ kỹ năng',
  agent_run: 'Từ lượt chạy',
};

export default function SessionsPage() {
  const { routeId } = useAppState();
  return routeId ? <NotebookPage workspaceId={routeId} /> : <SessionList />;
}

function SessionList() {
  const { navigate } = useAppState();
  const toast = useToast();
  const workspaces = useV1(['listWorkspaces'], listWorkspaces);
  const [creating, setCreating] = useState(false);

  const newNotebook = async () => {
    setCreating(true);
    try {
      const ws = await createBlankWorkspace('Notebook mới');
      navigate('analysis', 'SESSIONS', ws.id);
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-xl px-da-3xl py-da-xl">
      <PageHeader
        title="Phiên làm việc"
        description="Notebook của bạn và đồng nghiệp, cùng các agent đang làm trong đó."
        actions={
          <>
            <LinkButton area="analysis" page="ASK">
              Hỏi mới
            </LinkButton>
            <Button kind="primary" loading={creating} onClick={newNotebook}>
              Notebook mới
            </Button>
          </>
        }
      />
      <AsyncRegion
        state={workspaces}
        what="phiên làm việc"
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState
            title="Chưa có phiên nào"
            description="Mở notebook trống, hoặc hỏi một câu rồi lưu câu trả lời vào phiên."
            action={
              <div className="flex gap-da-sm">
                <Button kind="primary" loading={creating} onClick={newNotebook}>
                  Notebook mới
                </Button>
                <LinkButton area="analysis" page="ASK">
                  Hỏi mới
                </LinkButton>
              </div>
            }
          />
        }
      >
        {(page) => (
          <ul className="flex flex-col rounded-lg border border-border bg-card px-da-xl">
            {page.items.map((ws) => (
              <li key={ws.id} className="border-b border-border last:border-b-0">
                <button
                  type="button"
                  onClick={() => navigate('analysis', 'SESSIONS', ws.id)}
                  className="flex w-full items-center gap-da-md py-da-md text-left hover:bg-subtle"
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-foreground da-body-strong">{ws.title}</span>
                    <span className="text-muted-foreground da-caption">
                      {[
                        FROM_LABEL[ws.created_from],
                        `${ws.tile_count} ô`,
                        (ws.participants?.people ?? 0) > 1
                          ? `${ws.participants?.people} người`
                          : null,
                        (ws.participants?.agents ?? 0) > 0
                          ? `${ws.participants?.agents} agent`
                          : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  <SessionState ws={ws} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </AsyncRegion>
    </div>
  );
}

function SessionState({ ws }: { ws: Workspace }) {
  if (ws.agent_state === 'waiting_you') return <Badge tone="warning">Chờ bạn</Badge>;
  if (ws.agent_state === 'running') return <StatusDot tone="info" label="Agent đang chạy" />;
  return (
    <span className="shrink-0 text-muted-foreground da-caption">
      {formatElapsed(ws.updated_at)}
    </span>
  );
}
