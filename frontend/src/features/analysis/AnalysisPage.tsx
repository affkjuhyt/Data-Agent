import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { LinkCard } from '../../design-system/LinkCard';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { formatElapsed } from '../../lib/formatVN';
import { listDatasets, listWorkspaces } from '../../lib/v1/api';
import type { Workspace } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { AskBar } from '../home/AskBar';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "v3.1 · Phân tích" (2155:16027, UC-01-01, UC-02-01): where analysis starts. 16×32 padding,
 * 20px gap: header with actions, the question bar, four ways to start, recent sessions, and the
 * certified datasets. With no session yet the recent list gives way to "v3.1 · Phân tích · Trống"
 * (2165:17070), which points to Khám phá.
 */
const PENDING = 'Màn này đang được làm.';

const FROM_LABEL: Record<Workspace['created_from'], string> = {
  blank: 'Notebook',
  conversation: 'Hội thoại',
  template: 'Từ kỹ năng',
  agent_run: 'Từ lượt chạy',
};

export default function AnalysisPage() {
  const { navigate } = useAppState();
  const workspaces = useV1(['listWorkspaces'], listWorkspaces);
  const datasets = useV1(['listDatasets'], listDatasets);

  const ask = (question: string) => {
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(question)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <div className="flex flex-col gap-[20px] px-da-3xl py-da-xl">
      <PageHeader
        title="Phân tích"
        description="Hỏi, khám phá, làm notebook và dashboard trên cùng các chỉ số đã chứng nhận."
        actions={
          <>
            <Tooltip content={PENDING}>
              <Button disabled>Dashboard mới</Button>
            </Tooltip>
            <LinkButton area="analysis" page="ASK" kind="primary">
              Phiên mới
            </LinkButton>
          </>
        }
      />

      <AskBar
        suggestions={[]}
        onAsk={ask}
        placeholder="Hỏi về dữ liệu, ví dụ “dư nợ tiêu dùng theo vùng tháng này”"
      />

      <div className="grid grid-cols-4 gap-da-lg max-[1023px]:grid-cols-2">
        <LinkCard
          icon="chart"
          title="Khám phá kéo thả"
          corner="chevron"
          onOpen={() => navigate('analysis', 'EXPLORE')}
        >
          <span className="text-muted-foreground da-caption">
            Chọn chỉ số và chiều, không cần SQL
          </span>
        </LinkCard>
        <LinkCard
          icon="chat"
          title="Hỏi bằng lời"
          corner="chevron"
          onOpen={() => navigate('analysis', 'ASK')}
        >
          <span className="text-muted-foreground da-caption">
            Agent trả lời và hỏi lại khi mơ hồ
          </span>
        </LinkCard>
        <LinkCard
          icon="layers"
          title="Notebook"
          corner="chevron"
          onOpen={() => navigate('analysis', 'SESSIONS')}
        >
          <span className="text-muted-foreground da-caption">
            Kết hợp câu hỏi, SQL, biểu đồ với agent
          </span>
        </LinkCard>
        <LinkCard
          icon="file"
          title="Báo cáo định kỳ"
          corner="chevron"
          onOpen={() => navigate('analysis', 'REPORTS')}
        >
          <span className="text-muted-foreground da-caption">
            Agent soạn, bạn duyệt rồi phát hành
          </span>
        </LinkCard>
      </div>

      {workspaces.data && workspaces.data.items.length === 0 ? (
        <EmptyState
          icon="chart"
          title="Chưa có dashboard hay phiên nào"
          description="Hỏi một câu ở ô phía trên, hoặc mở Khám phá để kéo thả chỉ số đầu tiên."
          className="rounded-lg border border-border bg-card"
          action={
            <div className="flex gap-da-sm">
              <LinkButton area="analysis" page="EXPLORE" kind="primary">
                Mở Khám phá
              </LinkButton>
              <LinkButton area="home" page="HOW_IT_WORKS">
                Xem ví dụ
              </LinkButton>
            </div>
          }
        />
      ) : (
        <Card title="Phiên gần đây">
          <AsyncRegion
            state={workspaces}
            what="phiên làm việc"
            isEmpty={(page) => page.items.length === 0}
            empty={
              <p className="text-muted-foreground da-body-small">
                Chưa có phiên nào. Bấm “Phiên mới” để bắt đầu.
              </p>
            }
          >
            {(page) => (
              <ul className="flex flex-col">
                {page.items.slice(0, 5).map((ws) => (
                  <li key={ws.id}>
                    <button
                      type="button"
                      onClick={() => navigate('analysis', 'SESSIONS')}
                      className="flex w-full items-center gap-da-md border-b border-border py-da-sm text-left hover:bg-subtle"
                    >
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-foreground da-body-strong">{ws.title}</span>
                        <span className="text-muted-foreground da-caption">
                          {[
                            FROM_LABEL[ws.created_from],
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
        </Card>
      )}

      <section aria-labelledby="certified-title" className="flex flex-col gap-da-md">
        <h2 id="certified-title" className="text-foreground da-heading-3">
          Bộ dữ liệu đã chứng nhận
        </h2>
        <AsyncRegion state={datasets} what="bộ dữ liệu">
          {(page) => (
            <div className="flex flex-wrap gap-da-md">
              {page.items
                .filter((d) => d.certified)
                .map((d) => (
                  <span
                    key={d.id}
                    className="inline-flex items-center gap-da-sm rounded-lg border border-border bg-card px-da-md py-da-xs text-foreground da-body-small"
                  >
                    <Icon name="check" className="text-success" />
                    {d.display_name}
                  </span>
                ))}
            </div>
          )}
        </AsyncRegion>
      </section>
    </div>
  );
}

function SessionState({ ws }: { ws: Workspace }) {
  if (ws.agent_state === 'waiting_you') return <StatusDot tone="warning">Agent chờ bạn</StatusDot>;
  if (ws.agent_state === 'running') return <StatusDot tone="info">Agent đang chạy</StatusDot>;
  const ago = ws.last_refreshed_at
    ? (Date.now() - new Date(ws.last_refreshed_at).getTime()) / 1000
    : null;
  return ago !== null ? <Badge>Cập nhật {formatElapsed(ago)} trước</Badge> : null;
}
