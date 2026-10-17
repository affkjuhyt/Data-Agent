import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { listRuns } from '../../lib/v1/api';
import type { AgentRun } from '../../lib/v1/types';
import {
  activateAgent,
  getAgent,
  putTriggers,
  saveConfig,
  type AgentConfig,
  type AgentDetail,
  type AgentTrigger,
  type Autonomy,
  type Checkpoint,
  type TriggerKind,
} from '../../lib/v1/agentBuilder';
import { useV1 } from '../../lib/v1/useV1';
import { agentStatus } from '../agents/agentStatus';
import { runStatus } from '../agents/runStatus';
import { MemoryTab } from './MemoryTab';
import { TestRunDrawer, useTestRun } from './TestRunDrawer';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * One agent, addressed /agents/agent/<id>, with the tabs of the P13 agent frames: "Cấu hình"
 * (2309:20146, UC-13-02/03: triggers, autonomy, when to stop and ask, permissions and tools) and
 * "Bộ nhớ" (2310:20345, UC-13-07). "Lượt chạy" lists this agent's runs. "Tổng quan" and
 * "Đánh giá" have no frame yet.
 *
 * Saving never edits a version in place (BR-69): config changes become version n+1, which must
 * pass its test suite before it runs for real; triggers are replaced as a whole (PUT).
 */
type Tab = 'overview' | 'config' | 'runs' | 'memory' | 'eval';

const TABS: { value: Tab; label: string }[] = [
  { value: 'overview', label: 'Tổng quan' },
  { value: 'config', label: 'Cấu hình' },
  { value: 'runs', label: 'Lượt chạy' },
  { value: 'memory', label: 'Bộ nhớ' },
  { value: 'eval', label: 'Đánh giá' },
];

const AUTONOMY: { value: Autonomy; label: string }[] = [
  { value: 'suggest_only', label: 'Chỉ gợi ý' },
  { value: 'propose_approve', label: 'Đề xuất, người duyệt' },
  { value: 'act_then_report', label: 'Tự làm, báo sau' },
];

const TRIGGER_KIND: Record<TriggerKind, string> = {
  schedule: 'Theo lịch',
  event: 'Sự kiện',
  threshold: 'Ngưỡng',
  agent_call: 'Agent khác gọi',
  manual: 'Thủ công',
};

const HISTORY_PENDING = 'Màn lịch sử phiên bản đang được làm.';

export default function AgentConfigPage() {
  const { routeId } = useAppState();
  const agent = useV1(routeId ? () => getAgent(routeId) : null, [routeId]);
  // A agent in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(agent.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={agent} what="agent">
      {(detail) =>
        detail.config ? (
          <AgentBody
            key={detail.config.id}
            agent={{ ...detail, config: detail.config }}
            onSaved={agent.reload}
          />
        ) : (
          <EmptyState
            title={`${detail.name} chưa có cấu hình`}
            description="Agent chưa có phiên bản cấu hình nào để xem hay chạy thử."
          />
        )
      }
    </AsyncRegion>
  );
}

function AgentBody({
  agent,
  onSaved,
}: {
  agent: AgentDetail & { config: AgentConfig };
  onSaved: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('config');
  const [autonomy, setAutonomy] = useState<Autonomy>(agent.config.autonomy);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>(agent.config.checkpoints);
  const [triggers, setTriggers] = useState<AgentTrigger[]>(agent.triggers);
  const [addingMemory, setAddingMemory] = useState(false);
  const [busy, setBusy] = useState<'save' | 'activate' | null>(null);
  const [error, setError] = useState<string>();
  const test = useTestRun();

  const configDirty =
    autonomy !== agent.config.autonomy ||
    checkpoints.some((c, i) => c.enabled !== agent.config.checkpoints[i]?.enabled);
  const triggersDirty = triggers.some((t, i) => t.description !== agent.triggers[i]?.description);
  const nextVersion = agent.config.version + 1;

  // Leaving "Cấu hình" with unsaved edits asks first ("Xác nhận · cancelEdit" 2094:8561);
  // confirming drops the edits so coming back shows the saved version.
  const [leavingTo, setLeavingTo] = useState<Tab | null>(null);
  const changeTab = (next: Tab) => {
    if (tab === 'config' && next !== 'config' && (configDirty || triggersDirty)) setLeavingTo(next);
    else setTab(next);
  };
  const discard = () => {
    setAutonomy(agent.config.autonomy);
    setCheckpoints(agent.config.checkpoints);
    setTriggers(agent.triggers);
    if (leavingTo) setTab(leavingTo);
    setLeavingTo(null);
  };
  const status = agent.status === 'draft' ? { label: 'Nháp', tone: 'idle' as const } : agentStatus(agent);

  const save = async () => {
    setBusy('save');
    setError(undefined);
    try {
      if (triggersDirty) await putTriggers(agent.id, triggers);
      if (configDirty) {
        const { goal, denied_actions, tools } = agent.config;
        await saveConfig(agent.id, { goal, autonomy, checkpoints, denied_actions, tools });
      }
      toast({
        tone: 'success',
        message: configDirty ? `Đã lưu phiên bản v${nextVersion}` : 'Đã lưu trigger',
      });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const activate = async () => {
    if (!test.run) return;
    setBusy('activate');
    try {
      await activateAgent(agent.id, test.run.config_version);
      toast({ tone: 'success', message: 'Đã gửi yêu cầu kích hoạt cho chủ sở hữu dữ liệu' });
      test.close();
    } catch (e) {
      setError((e as Error).message);
      test.close();
    } finally {
      setBusy(null);
    }
  };

  const tabLabel = TABS.find((t) => t.value === tab)!.label;
  const actions =
    tab === 'config' ? (
      <>
        <Tooltip content={HISTORY_PENDING}>
          <Button kind="ghost" disabled>
            Lịch sử
          </Button>
        </Tooltip>
        <Button loading={test.starting} onClick={() => test.start(agent.id, agent.config.version)}>
          Chạy thử
        </Button>
        <Tooltip content={configDirty || triggersDirty ? 'Phiên bản mới phải qua bộ đánh giá trước khi chạy thật.' : 'Chưa có thay đổi.'}>
          <Button kind="primary" disabled={!configDirty && !triggersDirty} loading={busy === 'save'} onClick={save}>
            {configDirty || !triggersDirty ? `Lưu thành phiên bản v${nextVersion}` : 'Lưu trigger'}
          </Button>
        </Tooltip>
      </>
    ) : tab === 'memory' ? (
      <Button onClick={() => setAddingMemory(true)}>Thêm ghi nhớ</Button>
    ) : undefined;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', agent.name, tabLabel]}
        title={
          <span className="flex items-center gap-[10px]">
            {agent.name}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        actions={actions}
      />
      <Tabs<Tab> label="Phần của agent" items={TABS} value={tab} onChange={changeTab} className="pl-da-2xl" />
      {(error ?? test.error) && !test.run && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {error ?? test.error}</Notice>
        </div>
      )}

      {tab === 'config' && (
        <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
          <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
            <Section title="Khi nào chạy">
              <TriggerTable triggers={triggers} onChange={setTriggers} />
            </Section>
            <Section title="Mức tự chủ">
              <Segmented<Autonomy> variant="outline" label="Mức tự chủ" items={AUTONOMY} value={autonomy} onChange={setAutonomy} />
              <p className="text-muted-foreground da-caption">
                Lên “Tự làm, báo sau” cần bộ đánh giá đạt từ 95% và chủ sở hữu Data Space duyệt.
              </p>
            </Section>
            <Section title="Agent phải dừng và hỏi khi">
              {checkpoints.map((checkpoint) => (
                <Checkbox
                  key={checkpoint.id}
                  checked={checkpoint.enabled}
                  onChange={(enabled) =>
                    setCheckpoints((list) => list.map((c) => (c.id === checkpoint.id ? { ...c, enabled } : c)))
                  }
                >
                  {checkpoint.text}
                </Checkbox>
              ))}
            </Section>
          </div>
          <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0">
            <h2 className="text-foreground da-heading-3">Quyền và công cụ</h2>
            <Fact label="Chạy theo quyền của" value={`${agent.owner.name} (chủ sở hữu)`} />
            <Fact label="Dữ liệu" value={agent.config.data_scope_text} />
            <Fact label="Công cụ" value={agent.config.tools.map((t) => t.tool).join(', ')} />
            {agent.config.denied_actions.length > 0 && (
              <Fact label="Không được" value={agent.config.denied_actions.join(', ')} />
            )}
            <p className="text-foreground da-body-small">
              Lưu thay đổi tạo phiên bản mới; phiên bản mới phải qua bộ đánh giá trước khi chạy thật.
            </p>
          </aside>
        </div>
      )}

      {tab === 'memory' && (
        <MemoryTab agentId={agent.id} adding={addingMemory} onAddingDone={() => setAddingMemory(false)} />
      )}

      {tab === 'runs' && <AgentRuns agentId={agent.id} onOpen={(id) => navigate('agents', 'RUN', id)} />}

      {(tab === 'overview' || tab === 'eval') && (
        <div className="px-da-3xl pt-da-xl">
          <EmptyState
            icon="layers"
            title={tab === 'overview' ? 'Tổng quan agent đang được làm' : 'Đánh giá agent đang được làm'}
            description={
              tab === 'overview'
                ? 'Trong lúc chờ, xem cấu hình, lượt chạy và bộ nhớ ở các tab bên cạnh.'
                : 'Bộ thử và triển khai theo tỷ lệ lượt nằm ở Đánh giá & triển khai.'
            }
            action={tab === 'eval' ? <Button onClick={() => navigate('agents', 'EVALUATE')}>Mở Đánh giá & triển khai</Button> : undefined}
          />
        </div>
      )}

      {test.run && (
        <TestRunDrawer
          run={test.run}
          error={error ?? test.error}
          activating={busy === 'activate'}
          onActivate={activate}
          onEdit={() => {
            test.close();
            setTab('config');
          }}
          onClose={test.close}
        />
      )}
      <ConfirmDialog
        open={leavingTo !== null}
        title="Bỏ thay đổi chưa lưu?"
        confirmLabel="Bỏ thay đổi"
        onConfirm={discard}
        onCancel={() => setLeavingTo(null)}
      >
        Các thay đổi trong bản nháp sẽ mất.
      </ConfirmDialog>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col items-start gap-[10px] border-b border-border py-[14px]">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-[2px] pb-da-sm">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-body-small">{value}</span>
    </div>
  );
}

function RunsTable({ runs, onOpen }: { runs: AgentRun[]; onOpen: (id: string) => void }) {
  const view = useTableView(runs, {
    search: (run) => `${run.code} ${run.title} ${runStatus(run).label}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (run) => runStatus(run).label }],
    sorts: [
      { id: 'code', value: (run) => run.code },
      { id: 'title', value: (run) => run.title },
      { id: 'status', value: (run) => runStatus(run).label },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm lượt chạy…" searchLabel="Tìm lượt chạy" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="code" className="w-[80px] px-da-lg py-da-md font-normal">
              Lượt
            </SortableTh>
            <SortableTh view={view} column="title" className="px-da-lg py-da-md font-normal">
              Việc
            </SortableTh>
            <SortableTh view={view} column="status" className="w-[160px] px-da-lg py-da-md font-normal">
              Trạng thái
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((run) => {
            const s = runStatus(run);
            return (
              <tr key={run.id} className="border-b border-border align-middle da-body-small">
                <td className="px-da-lg py-[9px] text-muted-foreground da-mono">{run.code}</td>
                <td className="px-da-lg py-[9px]">
                  <button type="button" onClick={() => onOpen(run.id)} className="text-left text-foreground hover:underline">
                    {run.title}
                  </button>
                </td>
                <td className="px-da-lg py-[9px]">
                  <StatusDot tone={s.tone}>{s.label}</StatusDot>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}

function TriggerTable({ triggers, onChange }: { triggers: AgentTrigger[]; onChange: (next: AgentTrigger[]) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState('');

  const commit = (trigger: AgentTrigger) => {
    setEditing(null);
    const text = value.trim();
    if (!text || text === trigger.description) return;
    // A new description without cron/condition: the server parses the words (PROPOSAL).
    onChange(
      triggers.map((t) =>
        t.id === trigger.id ? { ...t, description: text, cron: null, condition: {} } : t,
      ),
    );
  };

  const view = useTableView(triggers, {
    search: (trigger) => `${TRIGGER_KIND[trigger.kind]} ${trigger.description}`,
    filters: [{ id: 'kind', label: 'Loại', value: (trigger) => TRIGGER_KIND[trigger.kind] }],
    sorts: [
      { id: 'kind', value: (trigger) => TRIGGER_KIND[trigger.kind] },
      { id: 'condition', value: (trigger) => trigger.description },
    ],
    pageSize: 50,
  });

  return (
    <>
    <TableToolbar view={view} placeholder="Tìm trigger…" searchLabel="Tìm trigger" />
    <table className="w-full table-fixed border-collapse">
      <thead>
        <tr className="border-b border-border text-left text-muted-foreground da-caption">
          <SortableTh view={view} column="kind" className="px-da-lg py-da-md font-normal">
            Trigger
          </SortableTh>
          <SortableTh view={view} column="condition" className="w-[300px] px-da-lg py-da-md font-normal">
            Điều kiện
          </SortableTh>
          <SortableTh view={view} className="w-[60px] px-da-lg py-da-md">
            <span className="sr-only">Thao tác</span>
          </SortableTh>
        </tr>
      </thead>
      <tbody>
        {view.rows.map((trigger) => (
          <tr key={trigger.id} className="border-b border-border align-middle da-body-small">
            <td className="px-da-lg py-[9px] text-foreground da-body-strong">{TRIGGER_KIND[trigger.kind]}</td>
            <td className="px-da-lg py-[9px] text-foreground">
              {editing === trigger.id ? (
                <input
                  autoFocus
                  aria-label={`Điều kiện ${TRIGGER_KIND[trigger.kind]}`}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onBlur={() => commit(trigger)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') commit(trigger);
                    if (event.key === 'Escape') setEditing(null);
                  }}
                  className="w-full rounded-sm border border-ring bg-card px-da-sm py-[2px] text-foreground outline-none da-body-small"
                />
              ) : (
                trigger.description
              )}
            </td>
            <td className="px-da-lg py-[9px]">
              {editing !== trigger.id && (
                <button
                  type="button"
                  onClick={() => {
                    setValue(trigger.description);
                    setEditing(trigger.id);
                  }}
                  aria-label={`Sửa trigger ${trigger.description}`}
                  className="rounded-sm text-primary da-body-strong hover:underline"
                >
                  Sửa
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    <TablePager view={view} />
    </>
  );
}

function AgentRuns({ agentId, onOpen }: { agentId: string; onOpen: (id: string) => void }) {
  const runs = useV1(() => listRuns({ agent_id: agentId }), [agentId]);
  return (
    <div className="px-da-3xl pt-da-xl">
      <AsyncRegion
        state={runs}
        what="lượt chạy"
        skeletonRows={4}
        isEmpty={(page) => !page.items.some((r) => r.agent_id === agentId)}
        empty={
          <EmptyState icon="play" title="Agent chưa chạy lượt nào" description="Lượt chạy hiện ở đây khi trigger khớp hoặc khi bạn giao việc." />
        }
      >
        {(page) => (
          <RunsTable runs={page.items.filter((r) => r.agent_id === agentId)} onOpen={onOpen} />
        )}
      </AsyncRegion>
    </div>
  );
}
