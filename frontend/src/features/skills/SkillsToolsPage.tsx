import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  listSkillSuggestions,
  listSkills,
  listTools,
  type Skill,
  type Tool,
} from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';
import { SKILL_TONE, TOOL_LEVELS, levelOf } from './labels';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "v3.1 · Kỹ năng & công cụ" (2057:1270, UC-03-01, UC-13-02) with its tab "Công cụ"
 * (2099:9862). Skills are reusable ways of working; tools are the system's catalogue of actions
 * an agent may call (GET /v1/tools), grouped by permission level, whose approval policy applies to
 * every agent; they are assigned through an agent's config, not registered here. External MCP
 * servers as tool providers have no use case (P12 specifies only Data Agent's own MCP server).
 * Opened from the rail item "Công cụ", so the tools tab comes first.
 */
type Tab = 'skills' | 'tools';

export default function SkillsToolsPage() {
  const { navigate } = useAppState();
  const skills = useV1(['listSkills'], () => listSkills());
  const tools = useV1(['listTools'], listTools);
  const [tab, setTab] = useState<Tab>('tools');

  return (
    <div className="flex flex-col gap-da-lg px-da-3xl py-da-xl">
      <PageHeader
        breadcrumb={['Agents', 'Kỹ năng & công cụ']}
        title="Kỹ năng & công cụ"
        description="Kỹ năng là cách làm có thể tái sử dụng; công cụ là hành động agent được phép gọi. Agent chỉ dùng được những gì được gán và trong mức quyền của công cụ."
        actions={
          <LinkButton area="analysis" page="SESSIONS">
            Tạo kỹ năng từ phiên
          </LinkButton>
        }
      />
      <Notice tone="info">
        Kỹ năng là cách làm agent dùng lại được; công cụ là việc agent được phép làm. Công cụ ghi dữ
        liệu hoặc gửi ra ngoài luôn cần duyệt.
      </Notice>
      <Tabs<Tab>
        label="Kỹ năng & công cụ"
        value={tab}
        onChange={setTab}
        items={[
          { value: 'skills', label: 'Kỹ năng', count: skills.data?.items.length },
          { value: 'tools', label: 'Công cụ', count: tools.data?.items.length },
        ]}
      />
      {tab === 'skills' && (
        <div className="flex items-start gap-da-lg">
          <div className="flex min-w-0 flex-1 flex-col gap-da-lg">
            <AsyncRegion
              state={skills}
              what="kỹ năng"
              isEmpty={(page) => page.items.length === 0}
              empty={
                <EmptyState
                  icon="layers"
                  title="Chưa có kỹ năng nào"
                  description="Biến một phiên phân tích thành kỹ năng để người và agent dùng lại."
                />
              }
            >
              {(page) => (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-da-md">
                  {page.items.map((s) => (
                    <SkillCard
                      key={s.id}
                      skill={s}
                      onOpen={() => navigate('agents', 'SKILL_RUN', s.id)}
                    />
                  ))}
                </div>
              )}
            </AsyncRegion>
            <SuggestionsCard />
          </div>
          <div className="w-[420px] shrink-0 max-[1279px]:hidden">
            <AsyncRegion state={tools} what="công cụ">
              {(page) => <ToolsByLevel tools={page.items} />}
            </AsyncRegion>
          </div>
        </div>
      )}
      {tab === 'tools' && (
        <AsyncRegion
          state={tools}
          what="công cụ"
          skeletonRows={6}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="plug"
              title="Chưa có công cụ nào"
              description="Danh mục công cụ của hệ thống chưa có công cụ nào."
            />
          }
        >
          {(page) => <ToolTable tools={page.items} />}
        </AsyncRegion>
      )}
    </div>
  );
}

function SkillCard({ skill, onOpen }: { skill: Skill; onOpen: () => void }) {
  const usage =
    skill.agent_count === 0
      ? 'chưa gán agent'
      : [
          `${skill.agent_count} agent`,
          skill.runs_30d !== null && `${formatNumber(skill.runs_30d)} lần`,
          skill.pass_rate_pct !== null && `đạt ${skill.pass_rate_pct}%`,
        ]
          .filter(Boolean)
          .join(' · ');
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex flex-col gap-da-sm rounded-lg border border-border bg-card p-da-lg text-left hover:border-border-strong"
    >
      <span className="flex w-full items-center gap-da-sm">
        <span className="min-w-0 flex-1 text-foreground da-heading-3">{skill.name}</span>
        <StatusDot tone={SKILL_TONE[skill.status]}>{skill.status_text}</StatusDot>
      </span>
      <span className="text-muted-foreground da-body-small">{skill.description}</span>
      <span className="flex gap-da-sm da-caption">
        <span className="text-foreground da-mono">{skill.step_count} bước</span>
        <span className="text-muted-foreground">·</span>
        <span className="text-muted-foreground">{usage}</span>
      </span>
    </button>
  );
}

/* "Agent đề xuất kỹ năng mới": the first suggestions, the full list is "Đề xuất từ agent". */
function SuggestionsCard() {
  const { navigate } = useAppState();
  const suggestions = useV1(['listSkillSuggestions'], listSkillSuggestions);
  const items = suggestions.data?.items.slice(0, 2) ?? [];
  if (suggestions.data && items.length === 0) return null;
  return (
    <section className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg">
      <div className="flex flex-col gap-da-xs">
        <h2 className="text-foreground da-heading-3">Agent đề xuất kỹ năng mới</h2>
        <p className="text-muted-foreground da-caption">
          Phát hiện từ cách người dùng và agent làm việc lặp lại trong 30 ngày.
        </p>
      </div>
      <AsyncRegion state={suggestions} what="đề xuất kỹ năng" skeletonRows={2}>
        {() => (
          <ul className="flex flex-col">
            {items.map((s) => (
              <li
                key={s.id}
                className="flex items-center gap-da-md border-t border-border py-da-sm"
              >
                <Icon name="agent" className="text-primary" />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-foreground da-body-strong">{s.title}</span>
                  <span className="text-muted-foreground da-caption">{s.steps.join(' → ')}</span>
                </span>
                <span className="shrink-0 text-primary da-caption">{s.count_text}</span>
                <LinkButton
                  area="agents"
                  page="SKILL_FROM_SESSION"
                  id={s.session_ids[0]}
                  disabled={s.session_ids.length === 0}
                >
                  Tạo kỹ năng
                </LinkButton>
              </li>
            ))}
          </ul>
        )}
      </AsyncRegion>
    </section>
  );
}

function ToolsByLevel({ tools }: { tools: Tool[] }) {
  return (
    <section className="flex flex-col gap-da-sm rounded-lg border border-border bg-card p-da-lg">
      <h2 className="text-foreground da-heading-3">Công cụ theo mức quyền</h2>
      <p className="text-muted-foreground da-caption">
        Quyền càng cao, chính sách duyệt càng chặt. Mức quyền áp chung cho mọi agent.
      </p>
      {TOOL_LEVELS.map((l) => {
        const own = tools.filter((t) => t.level === l.level);
        return (
          <div key={l.level} className="flex flex-col gap-da-xs border-t border-border py-da-md">
            <div className="flex items-center">
              <StatusDot tone={l.tone}>{l.label}</StatusDot>
              <span className="flex-1" />
              <span className="text-muted-foreground da-caption">{own.length} công cụ</span>
            </div>
            {own.length > 0 && (
              <ul className="flex flex-wrap gap-da-xs">
                {own.map((t) => (
                  <li
                    key={t.name}
                    className="rounded-sm bg-subtle px-da-sm py-da-xs text-foreground da-mono"
                  >
                    {t.name}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-muted-foreground da-caption">{l.policy}</p>
          </div>
        );
      })}
    </section>
  );
}

function ToolTable({ tools }: { tools: Tool[] }) {
  const view = useTableView(tools, {
    search: (t) => `${t.name} ${t.description} ${t.executed_by} ${levelOf(t.level).short}`,
    filters: [{ id: 'level', label: 'Mức quyền', value: (t) => levelOf(t.level).short }],
    sorts: [
      { id: 'name', value: (t) => t.name },
      { id: 'level', value: (t) => levelOf(t.level).short },
      { id: 'desc', value: (t) => t.description },
      { id: 'by', value: (t) => t.executed_by },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm công cụ…" searchLabel="Tìm công cụ" />
      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full table-fixed border-collapse">
          <thead>
            <tr className="bg-subtle text-left text-muted-foreground da-caption">
              <SortableTh view={view} column="name" className="w-[202px] px-da-md py-da-md font-normal">
                Công cụ
              </SortableTh>
              <SortableTh view={view} column="level" className="w-[162px] px-da-md py-da-md font-normal">
                Mức quyền
              </SortableTh>
              <SortableTh view={view} column="desc" className="px-da-md py-da-md font-normal">
                Mô tả
              </SortableTh>
              <SortableTh view={view} column="by" className="w-[160px] px-da-md py-da-md font-normal">
                Thực thi bởi
              </SortableTh>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((t) => {
              const level = levelOf(t.level);
              return (
                <tr key={t.name} className="border-t border-border">
                  <td className="px-da-md py-[6px] text-foreground da-mono">{t.name}</td>
                  <td className="px-da-md py-[6px]">
                    <StatusDot tone={level.tone}>{level.short}</StatusDot>
                  </td>
                  <td className="px-da-md py-[6px] text-foreground da-body-small">{t.description}</td>
                  <td className="px-da-md py-[6px] text-foreground da-mono">{t.executed_by}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}
