import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Menu } from '../../design-system/Menu';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { formatMoneyShort } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listAgents } from '../../lib/v1/api';
import type { AgentDef } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { agentStatus, sevenDaysText } from './agentStatus';

/*
 * "P13 · Đội agent" (2309:19811, UC-13-01): every agent of the Data Space in one dense table.
 * Bordered page header (breadcrumb, title, actions, one summary line), then rows of 9×12 with
 * the columns of the design: agent and goal, what it is doing now (warning when it waits on
 * someone, error when it stopped), when it runs, what it may do, last 7 days, status.
 *
 * "Tạo agent" opens the existing agent builder until "P13 · Tạo agent từ mô tả" is built;
 * "Từ mẫu" waits for the template screen.
 */
const NOW_TONE = {
  normal: 'text-foreground',
  waiting: 'text-warning',
  error: 'text-error',
} as const;
const TEMPLATE_PENDING = 'Màn tạo agent từ mẫu đang được làm.';

export default function AgentTeamPage() {
  const { tenant, navigate } = useAppState();
  const agents = useV1(listAgents, []);
  const items = agents.data?.items ?? [];

  const summary = (() => {
    if (!agents.data) return undefined;
    const statuses = items.map(agentStatus);
    const count = (label: string) => statuses.filter((s) => s.label === label).length;
    const spent = items.reduce((sum, a) => sum + Number(a.spent_this_month || 0), 0);
    return [
      `${items.length} agent trong ${tenant.tenantName ?? 'Data Space này'}`,
      `${count('Đang chạy')} đang chạy`,
      `${count('Chờ bạn')} chờ bạn`,
      `${count('Lỗi')} lỗi`,
      `chi phí tháng này ${formatMoneyShort(spent)}`,
    ].join(' · ');
  })();

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Đội agent']}
        title="Đội agent"
        description={summary}
        actions={
          <>
            <Tooltip content={TEMPLATE_PENDING}>
              <Button disabled>Từ mẫu</Button>
            </Tooltip>
            <Button kind="primary" onClick={() => navigate('agents', 'BUILDER')}>
              Tạo agent
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        <AsyncRegion
          state={agents}
          what="đội agent"
          skeletonRows={5}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="agent"
              title="Chưa có agent nào"
              description="Agent làm việc thay bạn theo mục tiêu và lịch bạn đặt, trong giới hạn quyền của bạn."
            />
          }
        >
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">Agent</th>
                  <th className="w-[280px] px-da-lg py-da-md font-normal">Đang làm gì</th>
                  <th className="w-[170px] px-da-lg py-da-md font-normal max-[1439px]:hidden">
                    Khi nào chạy
                  </th>
                  <th className="w-[150px] px-da-lg py-da-md font-normal max-[1439px]:hidden">
                    Được phép
                  </th>
                  <th className="w-[110px] px-da-lg py-da-md font-normal">7 ngày</th>
                  <th className="w-[120px] px-da-lg py-da-md font-normal">Trạng thái</th>
                  <th className="w-[40px] px-da-lg py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((agent) => (
                  <AgentRow
                    key={agent.id}
                    agent={agent}
                    onRuns={() => navigate('agents', 'RUNS')}
                  />
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          Agent chỉ có quyền trong giới hạn của người sở hữu; mọi lượt chạy được ghi lại ở Lượt
          chạy.
        </p>
      </div>
    </div>
  );
}

function AgentRow({ agent, onRuns }: { agent: AgentDef; onRuns: () => void }) {
  const status = agentStatus(agent);
  return (
    <tr className="border-b border-border align-middle">
      <td className="px-da-lg py-[9px]">
        <div className="flex min-w-0 flex-col gap-[2px]">
          <span className="truncate text-foreground da-body-strong">{agent.name}</span>
          <span className="truncate text-muted-foreground da-caption">{agent.goal}</span>
        </div>
      </td>
      <td className={cn('px-da-lg py-[9px] da-body-small', NOW_TONE[agent.now.tone])}>
        {agent.now.text}
      </td>
      <td className="px-da-lg py-[9px] text-muted-foreground da-body-small max-[1439px]:hidden">
        {agent.trigger_text}
      </td>
      <td className="px-da-lg py-[9px] text-muted-foreground da-body-small max-[1439px]:hidden">
        {agent.permission_text}
      </td>
      <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">
        {sevenDaysText(agent)}
      </td>
      <td className="px-da-lg py-[9px]">
        <StatusDot tone={status.tone}>{status.label}</StatusDot>
      </td>
      <td className="px-da-xs py-[9px] text-right">
        <Menu
          label={`Thao tác với ${agent.name}`}
          items={[{ label: 'Xem lượt chạy', icon: 'play', onSelect: onRuns }]}
        />
      </td>
    </tr>
  );
}
