import { useState, type DragEvent, type KeyboardEvent } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { RunStep } from '../../design-system/RunStep';
import { Tabs } from '../../design-system/Tabs';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  getLatestTrial,
  getSkillDefinition,
  saveSkillDefinition,
  trySkill,
  type SkillDefinition,
  type SkillStepSpec,
  type SkillTrial,
} from '../../lib/v1/skillAuthoring';
import { useV1 } from '../../lib/v1/useV1';
import { KeyValue } from './KeyValue';

/*
 * "v3.1 · Kỹ năng · Trình soạn kỹ năng" (2089:7437, UC-03-03/04): the working version of a
 * skill — inputs and output, the steps and the tool each calls, the instructions the agent
 * follows, its safety limits — with the latest trial below and the facts and "Thử nhanh" on a
 * 320px side panel. The steps can be reordered (drag, or Alt+↑/↓) and the instructions edited;
 * "Lưu phiên bản" saves the draft. Kiểm thử and Phiên bản open their own screens; the
 * "Sử dụng" tab is not drawn: there is no endpoint listing the agents that use a skill.
 */
type Tab = 'definition' | 'tests' | 'versions';

export default function SkillEditorPage() {
  const { routeId } = useAppState();
  const definition = useV1(routeId ? () => getSkillDefinition(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={definition} what="kỹ năng">
      {(d) => <Editor key={`${d.skill_id}-${d.version}`} initial={d} onSaved={definition.reload} />}
    </AsyncRegion>
  );
}

function Editor({ initial, onSaved }: { initial: SkillDefinition; onSaved: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const id = initial.skill_id;
  const trialState = useV1(() => getLatestTrial(id), [id]);
  const [trial, setTrial] = useState<SkillTrial | null>();
  const [steps, setSteps] = useState(initial.steps);
  const [instructions, setInstructions] = useState(initial.instructions);
  const [quality, setQuality] = useState(initial.quality_text);
  const [busy, setBusy] = useState<'try' | 'save' | null>(null);
  const [error, setError] = useState<string>();
  const dirty =
    instructions !== initial.instructions ||
    steps.length !== initial.steps.length ||
    steps.some((s, i) => s.seq !== initial.steps[i]?.seq);

  const onTab = (tab: Tab) => {
    if (tab === 'tests') navigate('agents', 'SKILL_TESTS', id);
    if (tab === 'versions') navigate('agents', 'SKILL_VERSIONS', id);
  };

  const runTrial = async () => {
    setBusy('try');
    setError(undefined);
    try {
      const result = await trySkill(id, initial.quick_test.params);
      setTrial(result);
      setQuality(`v3: 96% · v${result.version}: đã chạy thử`);
      toast({
        tone: 'success',
        message: `Đã chạy thử v${result.version} · ${result.duration_text}`,
      });
    } catch (e) {
      setError(`Không chạy thử được: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save');
    setError(undefined);
    try {
      const saved = await saveSkillDefinition(id, { instructions, steps: renumber(steps) });
      setSteps(saved.steps);
      setInstructions(saved.instructions);
      toast({ tone: 'success', message: `Đã lưu v${initial.version} (nháp)` });
      onSaved();
    } catch (e) {
      setError(`Không lưu được: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const draft = initial.status === 'draft';
  return (
    <div className="flex min-h-full max-[1279px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-2xl py-da-lg">
        <PageHeader
          breadcrumb={['Agents', 'Kỹ năng & công cụ', initial.name]}
          title={`${initial.name} · v${initial.version}${draft ? ' (nháp)' : ''}`}
          actions={
            <>
              <Button
                icon="play"
                loading={busy === 'try'}
                disabled={busy !== null}
                onClick={runTrial}
              >
                Chạy thử
              </Button>
              <Button
                kind="primary"
                loading={busy === 'save'}
                disabled={busy !== null || !draft || !dirty}
                onClick={save}
              >
                Lưu phiên bản
              </Button>
            </>
          }
        />
        <Tabs<Tab>
          label="Phần của kỹ năng"
          value="definition"
          onChange={onTab}
          items={[
            { value: 'definition', label: 'Định nghĩa' },
            { value: 'tests', label: 'Kiểm thử', count: initial.test_count },
            { value: 'versions', label: 'Phiên bản', count: initial.version_count },
          ]}
        />
        {error && <Notice tone="error">{error}</Notice>}
        <div className="grid grid-cols-2 gap-da-md max-[1023px]:grid-cols-1">
          <div className="flex min-w-0 flex-col gap-da-md">
            <Card size="sm" title="Đầu vào & đầu ra" className="gap-da-sm px-da-lg py-da-md">
              {initial.inputs.map((input) => (
                <KeyValue key={input.name} label={input.name} width={120}>
                  {input.description}
                </KeyValue>
              ))}
              <KeyValue label="→ Đầu ra" width={120}>
                {initial.output_text}
              </KeyValue>
            </Card>
            <Card size="sm" title="Các bước" className="gap-da-sm px-da-lg py-da-md">
              <StepList steps={steps} onChange={setSteps} disabled={!draft} />
              {draft && (
                <AddStep
                  onAdd={(text) =>
                    setSteps((prev) => [...prev, { seq: nextSeq(prev), text, tool: null }])
                  }
                />
              )}
            </Card>
          </div>
          <div className="flex min-w-0 flex-col gap-da-md">
            <Card size="sm" title="Hướng dẫn cho agent" className="gap-da-sm px-da-lg py-da-md">
              <Textarea
                aria-label="Hướng dẫn cho agent"
                value={instructions}
                readOnly={!draft}
                onChange={(e) => setInstructions(e.target.value)}
                rows={4}
                className="w-full"
              />
            </Card>
            <Card size="sm" title="Giới hạn an toàn" className="gap-da-sm px-da-lg py-da-md">
              <ul className="flex flex-col gap-da-sm">
                {initial.guardrails.map((g) => (
                  <li
                    key={g.text}
                    className="flex items-center gap-da-sm text-foreground da-body-small"
                  >
                    <Icon
                      name={g.kind === 'check' ? 'check' : 'lock'}
                      label={g.kind === 'check' ? 'Kiểm tra khi chạy' : 'Điều kiện chặn'}
                    />
                    {g.text}
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
        <AsyncRegion
          state={
            trial === undefined ? trialState : { ...trialState, data: trial, error: undefined }
          }
          what="lần chạy thử"
          isEmpty={(t) => t === null}
          empty={
            <p className="text-muted-foreground da-body-small">
              Bản này chưa chạy thử. Bấm “Chạy thử” để chạy một lần với tham số Thử nhanh.
            </p>
          }
        >
          {(t) => t && <TrialResult trial={t} />}
        </AsyncRegion>
      </div>

      <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-card px-da-xl py-da-lg max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t">
        <h2 className="text-foreground da-heading-3">Thông tin</h2>
        <KeyValue label="Chủ sở hữu" width={90}>
          {initial.owner.name}
        </KeyValue>
        <KeyValue label="Phạm vi" width={90}>
          {initial.scope_label}
        </KeyValue>
        <KeyValue label="Domain" width={90}>
          {initial.domain_text}
        </KeyValue>
        <KeyValue label="Dùng bởi" width={90}>
          {initial.used_by_text}
        </KeyValue>
        <KeyValue label="Chất lượng" width={90}>
          {quality}
        </KeyValue>
        <div className="flex flex-col items-start gap-da-xs rounded-md border border-border p-da-md">
          <span className="text-primary da-label">Thử nhanh</span>
          {initial.quick_test.lines.length > 0 ? (
            <pre className="w-full whitespace-pre-wrap text-foreground da-mono">
              {initial.quick_test.lines.join('\n')}
            </pre>
          ) : (
            <p className="text-muted-foreground da-caption">Chưa có tham số thử.</p>
          )}
          <Button
            kind="primary"
            icon="play"
            loading={busy === 'try'}
            disabled={busy !== null}
            onClick={runTrial}
          >
            Chạy thử
          </Button>
        </div>
      </aside>
    </div>
  );
}

const nextSeq = (steps: SkillStepSpec[]) => Math.max(0, ...steps.map((s) => s.seq)) + 1;

/* A new step is one the model does itself; the tool it calls is chosen from the library later. */
function AddStep({ onAdd }: { onAdd: (text: string) => void }) {
  const [text, setText] = useState('');
  const add = () => {
    if (!text.trim()) return;
    onAdd(text.trim());
    setText('');
  };
  return (
    <div className="flex items-center gap-da-sm">
      <Input
        aria-label="Bước mới"
        placeholder="Thêm một bước…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && add()}
      />
      <Button size="sm" icon="plus" disabled={!text.trim()} onClick={add}>
        Thêm bước
      </Button>
    </div>
  );
}

const renumber = (steps: SkillStepSpec[]) => steps.map((s, i) => ({ ...s, seq: i + 1 }));

/* Steps keep their number while being moved so a reader can follow which one moved. */
function StepList({
  steps,
  onChange,
  disabled,
}: {
  steps: SkillStepSpec[];
  onChange: (steps: SkillStepSpec[]) => void;
  disabled: boolean;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= steps.length || from === to) return;
    const next = [...steps];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    if (!event.altKey) return;
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      move(index, index + (event.key === 'ArrowUp' ? -1 : 1));
    }
  };
  return (
    <ol className="flex flex-col gap-da-sm" aria-label="Các bước · Alt+↑/↓ để đổi thứ tự">
      {steps.map((step, index) => (
        <li
          key={step.seq}
          tabIndex={disabled ? undefined : 0}
          draggable={!disabled}
          onKeyDown={(e) => onKeyDown(e, index)}
          onDragStart={() => setDragging(index)}
          onDragEnd={() => setDragging(null)}
          onDragOver={(e: DragEvent) => e.preventDefault()}
          onDrop={() => {
            if (dragging !== null) move(dragging, index);
            setDragging(null);
          }}
          className={cn(
            'flex items-center gap-da-sm rounded-sm outline-none focus-visible:shadow-[var(--focus-ring)]',
            !disabled && 'cursor-grab',
            dragging === index && 'opacity-50',
          )}
        >
          <span aria-hidden className="text-muted-foreground da-mono">
            ⋮⋮
          </span>
          <span className="text-muted-foreground da-mono">{index + 1}</span>
          <span className="min-w-0 flex-1 text-foreground da-body-small">{step.text}</span>
          {step.tool && <Badge>{step.tool}</Badge>}
        </li>
      ))}
    </ol>
  );
}

function TrialResult({ trial }: { trial: SkillTrial }) {
  const max = Math.max(...trial.bars.map((b) => Math.abs(b.value)), 0.0001);
  return (
    <section className="flex gap-da-2xl rounded-lg border border-border bg-card px-da-lg py-da-md max-[1023px]:flex-col">
      <div className="flex w-[360px] shrink-0 flex-col gap-da-sm max-[1023px]:w-full">
        <h2 className="text-foreground da-heading-3">
          Lần chạy thử gần nhất · v{trial.version} · {trial.duration_text}
        </h2>
        {trial.steps.map((s) => (
          <RunStep key={s.title} state={s.state} title={s.title} detail={s.detail} duration="" />
        ))}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-da-md">
        <span className="text-muted-foreground da-label">Đầu ra</span>
        {trial.bars.map((bar) => (
          <div
            key={bar.label}
            className="flex items-center gap-da-lg"
            title={`${bar.label}: ${bar.value_text}`}
          >
            <span className="w-[110px] shrink-0 text-foreground da-body-small">{bar.label}</span>
            <span className="min-w-0 flex-1">
              <span
                className={cn('block h-3 rounded-[3px]', bar.main ? 'bg-chart-3' : 'bg-chart-6')}
                style={{ width: `${(Math.abs(bar.value) / max) * 100}%` }}
              />
            </span>
            <span className="w-12 shrink-0 text-muted-foreground da-mono">{bar.value_text}</span>
          </div>
        ))}
        <p className="border-b border-border px-da-md py-da-sm text-foreground da-body-small">
          {trial.conclusion}
        </p>
      </div>
    </section>
  );
}
