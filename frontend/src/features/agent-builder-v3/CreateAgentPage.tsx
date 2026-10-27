import { useEffect, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  activateAgent,
  createAgent,
  draftAgent,
  listTemplates,
  saveConfig,
  type AgentDetail,
  type AgentDraft,
  type DraftField,
  type DraftLine,
} from '../../lib/v1/agentBuilder';
import { useV1 } from '../../lib/v1/useV1';
import { TestRunDrawer, useTestRun } from './TestRunDrawer';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "P13 · Tạo agent từ mô tả" (2309:19992, UC-13-01/02): describe the job in words, or start
 * from a template, and the config agent proposes name, goal, trigger, data, skills, recipients,
 * permissions and when to ask (POST /v1/agents:draft-from-text). Each line is edited in place;
 * a correction is sent back with the description so the proposal stays consistent.
 *
 * "Chạy thử một lần" needs an agent to run, so it creates the agent as a draft first, then opens
 * the test-run drawer (2072:4944). A new agent stays at "đề xuất" until it passes its test suite.
 */
const DRAFT_DELAY_MS = 800;
const MIN_DESCRIPTION = 20;
const NEED_DESCRIPTION = 'Mô tả việc bạn muốn giao trước.';

/*
 * "Biến phiên thành agent" (UC-03-03 → UC-13-01) hands the description over as
 * ?description=…&from_session=…; take it once and drop it from the URL so a reload starts clean.
 * from_session has no field in POST /v1/agents yet, so it is only dropped.
 */
function takeHandover(): string {
  const params = new URLSearchParams(window.location.search);
  const text = params.get('description');
  if (text === null) return '';
  params.delete('description');
  params.delete('from_session');
  const rest = params.toString();
  window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`);
  return text;
}

export default function CreateAgentPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const templates = useV1(['listTemplates'], listTemplates);
  const [description, setDescription] = useState(takeHandover);
  const [templateRef, setTemplateRef] = useState<string>();
  const [settled, setSettled] = useState(() => description.trim());
  const [corrections, setCorrections] = useState<Partial<Record<DraftField, string>>>({});

  // Re-draft once typing pauses, not on every keystroke (each draft is an LLM call).
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(description.trim()), DRAFT_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [description]);

  const ready = settled.length >= MIN_DESCRIPTION;
  const draft = useV1(['draftAgent', settled, templateRef, corrections], ready ? () => draftAgent({ description: settled, template_ref: templateRef, corrections }) : null);

  // The agent made for a test run, and the proposal it was made from.
  const created = useRef<{ agent: AgentDetail; from: AgentDraft } | null>(null);
  const [busy, setBusy] = useState<'test' | 'create' | 'activate' | null>(null);
  const [error, setError] = useState<string>();
  const test = useTestRun();

  const ensureAgent = async (proposal: AgentDraft): Promise<AgentDetail> => {
    const known = created.current;
    if (known && known.from === proposal) return known.agent;
    if (known) {
      const { goal, autonomy, checkpoints, denied_actions, tools } = proposal.config;
      const config = await saveConfig(known.agent.id, {
        goal,
        autonomy,
        checkpoints,
        denied_actions,
        tools,
        change_note: 'Sửa trước khi tạo',
      });
      created.current = { agent: { ...known.agent, config }, from: proposal };
    } else {
      created.current = { agent: await createAgent(proposal, templateRef), from: proposal };
    }
    return created.current.agent;
  };

  const run = async (kind: 'test' | 'create') => {
    if (!draft.data) return;
    setBusy(kind);
    setError(undefined);
    try {
      const agent = await ensureAgent(draft.data);
      if (kind === 'test') {
        await test.start(agent.id, agent.config?.version ?? 1);
      } else {
        toast({ tone: 'success', message: `Đã tạo ${agent.name}` });
        navigate('agents', 'AGENT', agent.id);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const activate = async () => {
    const agent = created.current?.agent;
    if (!agent || !test.run) return;
    setBusy('activate');
    try {
      await activateAgent(agent.id, test.run.config_version);
      toast({ tone: 'success', message: 'Đã gửi yêu cầu kích hoạt cho chủ sở hữu dữ liệu' });
      navigate('agents', 'AGENT', agent.id);
    } catch (e) {
      setError((e as Error).message);
      test.close();
    } finally {
      setBusy(null);
    }
  };

  const pickTemplate = (ref: string, text: string) => {
    setTemplateRef(ref);
    setDescription(text);
    setSettled(text);
    setCorrections({});
  };

  const correct = (field: DraftField, text: string) =>
    setCorrections((current) => ({ ...current, [field]: text }));

  const blocked = !draft.data;
  const actionError = error ?? test.error;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Tạo agent']}
        title="Tạo agent"
        actions={
          <>
            <LinkButton area="agents" page="TEAM" kind="ghost">
              Huỷ
            </LinkButton>
            <Tooltip
              content={
                blocked ? NEED_DESCRIPTION : 'Chạy trên dữ liệu tuần trước, không gửi gì ra ngoài.'
              }
            >
              <Button
                disabled={blocked}
                loading={busy === 'test' || test.starting}
                onClick={() => run('test')}
              >
                Chạy thử một lần
              </Button>
            </Tooltip>
            <Tooltip
              content={
                blocked
                  ? NEED_DESCRIPTION
                  : 'Agent mới ở mức “đề xuất” cho tới khi qua bộ đánh giá.'
              }
            >
              <Button
                kind="primary"
                disabled={blocked}
                loading={busy === 'create'}
                onClick={() => run('create')}
              >
                Tạo agent
              </Button>
            </Tooltip>
          </>
        }
      />
      {actionError && !test.run && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {actionError}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <section className="flex w-[480px] shrink-0 flex-col gap-[14px] border-r border-border pl-da-3xl pr-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-r-0 max-[1023px]:pb-da-xl">
          <label htmlFor="agent-description" className="text-foreground da-heading-3">
            Bạn muốn agent làm gì?
          </label>
          <Textarea
            id="agent-description"
            value={description}
            onChange={(event) => {
              setDescription(event.target.value);
              setCorrections({});
            }}
            rows={5}
            placeholder="Ví dụ: mỗi sáng thứ Hai, xem nợ quá hạn theo chi nhánh và gửi tóm tắt cho tôi qua Teams."
            className="w-full"
          />
          <p className="text-muted-foreground da-caption">Hoặc bắt đầu từ mẫu</p>
          <AsyncRegion state={templates} what="mẫu agent" skeletonRows={3}>
            {(list) => (
              <ul className="flex flex-col gap-[14px]">
                {list.items.map((template) => (
                  <li key={template.ref}>
                    <button
                      type="button"
                      onClick={() => pickTemplate(template.ref, template.description)}
                      aria-pressed={templateRef === template.ref}
                      className={cn(
                        'flex w-full items-center gap-da-md rounded-sm border px-da-lg py-da-md text-left hover:bg-subtle',
                        templateRef === template.ref
                          ? 'border-border-strong bg-subtle'
                          : 'border-border',
                      )}
                    >
                      <span className="min-w-0 flex-1 text-foreground da-body-small">
                        {template.title}
                      </span>
                      <span className="shrink-0 text-muted-foreground da-caption">
                        {template.pack}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </AsyncRegion>
        </section>

        <section className="flex min-w-0 flex-1 flex-col bg-background px-da-3xl pb-da-2xl pt-da-md">
          <h2 className="pb-da-xs pt-da-lg text-muted-foreground da-caption">
            Agent hiểu như sau · sửa trực tiếp nếu chưa đúng
          </h2>
          {ready ? (
            <AsyncRegion state={draft} what="đề xuất cấu hình" skeletonRows={8}>
              {(proposal) => <Proposal proposal={proposal} onCorrect={correct} />}
            </AsyncRegion>
          ) : (
            <p className="py-da-md text-muted-foreground da-body-small">
              Mô tả việc bạn muốn giao bằng lời; agent cấu hình sẽ đề xuất mục tiêu, dữ liệu, kỹ
              năng, trigger và mức tự chủ ở đây.
            </p>
          )}
        </section>
      </div>

      {test.run && (
        <TestRunDrawer
          run={test.run}
          error={actionError}
          activating={busy === 'activate'}
          onActivate={activate}
          onEdit={test.close}
          onClose={test.close}
        />
      )}
    </div>
  );
}

function Proposal({ proposal, onCorrect }: { proposal: AgentDraft; onCorrect: (field: DraftField, text: string) => void }) {
  const view = useTableView(proposal.lines, {
    search: (line) => `${line.label} ${line.text}`,
    filters: [{ id: 'part', label: 'Phần', value: (line) => line.label }],
    sorts: [
      { id: 'part', value: (line) => line.label },
      { id: 'text', value: (line) => line.text },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm phần…" searchLabel="Tìm phần đề xuất" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="part" className="w-[150px] px-da-lg py-da-md font-normal">
              Phần
            </SortableTh>
            <SortableTh view={view} column="text" className="px-da-lg py-da-md font-normal">
              Đề xuất
            </SortableTh>
            <SortableTh view={view} className="w-[90px] px-da-lg py-da-md">
              <span className="sr-only">Lưu ý</span>
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((line) => (
            <ProposalRow key={line.field} line={line} onCorrect={(text) => onCorrect(line.field, text)} />
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
      <div className="flex flex-col gap-da-sm pt-[14px]">
        {proposal.warning && <p className="text-warning da-body-small">{proposal.warning}</p>}
        <p className="text-muted-foreground da-caption">
          Agent mới luôn ở mức tự chủ “đề xuất” cho tới khi qua bộ đánh giá.
        </p>
      </div>
    </>
  );
}

function ProposalRow({ line, onCorrect }: { line: DraftLine; onCorrect: (text: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(line.text);

  const commit = () => {
    setEditing(false);
    if (value.trim() && value.trim() !== line.text) onCorrect(value.trim());
    else setValue(line.text);
  };

  return (
    <tr className="border-b border-border align-middle">
      <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">{line.label}</td>
      <td className="px-da-lg py-[5px]">
        {editing ? (
          <input
            autoFocus
            aria-label={line.label}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.key === 'Enter') commit();
              if (event.key === 'Escape') {
                setValue(line.text);
                setEditing(false);
              }
            }}
            className="w-full rounded-sm border border-ring bg-card px-da-sm py-[3px] text-foreground outline-none da-body-small"
          />
        ) : (
          <button
            type="button"
            onClick={() => {
              setValue(line.text);
              setEditing(true);
            }}
            aria-label={`Sửa ${line.label}: ${line.text}`}
            className="w-full rounded-sm px-da-sm py-[3px] text-left text-foreground da-body-small hover:bg-subtle"
          >
            {line.text}
          </button>
        )}
      </td>
      <td className="px-da-lg py-[9px] text-warning da-caption">{line.flag}</td>
    </tr>
  );
}
