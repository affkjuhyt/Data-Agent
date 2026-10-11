import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Drawer } from '../../design-system/Drawer';
import { Icon } from '../../design-system/Icon';
import { Menu } from '../../design-system/Menu';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { cn } from '../../lib/utils';
import { estimateRunPlan, updateRunPlan, type PlanEdit } from '../../lib/v1/runPlan';
import type { AgentRun } from '../../lib/v1/types';

/*
 * "P13 · Can thiệp kế hoạch" (2070:3221, UC-13-06): a 560px drawer over the run, opened while the
 * run is stopped at a step boundary. Finished steps keep their results and cannot change; the rest
 * can be edited, reordered (drag the handle, or the row menu), dropped, and new steps added. A note
 * for the agent travels with the edit. Before applying, the server estimates what the change costs
 * and what it puts at risk downstream; applying saves the next plan version and runs on from the
 * first step not yet done.
 */
type Change = 'kept' | 'edited' | 'added' | 'removed';
interface DraftStep {
  key: string;
  id?: string;
  title: string;
  agent_name: string;
  number: number | null;
  change: Change;
}

const TAG: Partial<Record<Change, { label: string; tone: 'success' | 'error' }>> = {
  edited: { label: 'Đã sửa', tone: 'success' },
  added: { label: 'Thêm', tone: 'success' },
  removed: { label: 'Bỏ', tone: 'error' },
};
const ESTIMATE_DELAY_MS = 300;

export function PlanEditDrawer({
  run,
  onClose,
  onApplied,
}: {
  run: AgentRun;
  onClose: () => void;
  onApplied: (version: number) => void;
}) {
  const plan = run.plan ?? [];
  const done = plan.filter((s) => s.state === 'done');
  // Finished steps lead the plan; only the ones after them are drafted.
  const [steps, setSteps] = useState<DraftStep[]>(() =>
    plan.flatMap((s, i) =>
      s.state === 'done'
        ? []
        : [
            {
              key: s.id ?? `step-${i}`,
              id: s.id,
              title: s.title,
              agent_name: s.agent_name ?? run.agent_name,
              number: i + 1,
              change: 'kept' as const,
            },
          ],
    ),
  );
  const [note, setNote] = useState('');
  const [editing, setEditing] = useState<string>();
  const [dragging, setDragging] = useState<string>();
  const [estimate, setEstimate] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const agents = [...new Set(plan.map((s) => s.agent_name ?? run.agent_name))];
  const firstOpen = done.length + 1;
  const original = plan
    .slice(done.length)
    .map((s) => s.id)
    .join();
  const reordered = steps.flatMap((s) => (s.id ? [s.id] : [])).join() !== original;
  const changed = reordered || steps.some((s) => s.change !== 'kept') || note.trim() !== '';
  const edit: PlanEdit = {
    steps: [
      ...done.map((s) => ({
        id: s.id,
        title: s.title,
        agent_name: s.agent_name ?? run.agent_name,
      })),
      ...steps.map((s) => ({
        id: s.id,
        title: s.title,
        agent_name: s.agent_name,
        removed: s.change === 'removed' || undefined,
      })),
    ],
    edit_note: note.trim() || undefined,
  };
  const editKey = JSON.stringify(edit);

  // The estimate follows the draft; a stale answer is dropped rather than shown over a newer one.
  useEffect(() => {
    if (!changed) {
      setEstimate(undefined);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      estimateRunPlan(run.id, JSON.parse(editKey) as PlanEdit)
        .then((r) => live && setEstimate(r.text))
        .catch(() => live && setEstimate(undefined));
    }, ESTIMATE_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [editKey, changed, run.id]);

  const update = (key: string, patch: Partial<DraftStep>) =>
    setSteps((all) =>
      all.map((s) =>
        s.key === key
          ? {
              ...s,
              ...patch,
              change: patch.change ?? (s.change === 'added' ? 'added' : 'edited'),
            }
          : s,
      ),
    );

  const move = (key: string, to: number) =>
    setSteps((all) => {
      const from = all.findIndex((s) => s.key === key);
      if (from < 0 || to < 0 || to >= all.length || from === to) return all;
      const next = [...all];
      const [step] = next.splice(from, 1);
      next.splice(to, 0, step);
      return next;
    });

  const add = () => {
    const key = `new-${Date.now()}`;
    setSteps((all) => [
      ...all,
      { key, title: '', agent_name: run.agent_name, number: null, change: 'added' },
    ]);
    setEditing(key);
  };

  const apply = async () => {
    if (steps.some((s) => s.change !== 'removed' && !s.title.trim())) {
      setError('Đặt tên cho mọi bước trước khi áp dụng.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const plan = await updateRunPlan(run.id, edit);
      onApplied(plan.version);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      open
      width="lg"
      title={`Can thiệp kế hoạch · lượt ${run.code}`}
      description={`Agent đang dừng an toàn ở bước ${firstOpen}. Thay đổi áp dụng từ bước ${firstOpen}${
        done.length > 0
          ? `; kết quả bước ${done.length > 1 ? `1–${done.length}` : '1'} được giữ`
          : ''
      }.`}
      onClose={onClose}
      footer={
        <>
          <p className="min-w-0 flex-1 self-center text-muted-foreground da-caption">
            Lưu thành phiên bản kế hoạch {(run.plan_version ?? 1) + 1} · ghi vào nhật ký
          </p>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} disabled={busy || !changed} onClick={apply}>
            Áp dụng và chạy tiếp
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-da-md">
        {error && <Notice tone="error">Không áp dụng được: {error}</Notice>}
        <ol aria-label="Các bước của kế hoạch" className="flex flex-col gap-[4px]">
          {done.map((s, i) => (
            <li
              key={s.id ?? s.title}
              className="flex min-h-[48px] items-center gap-da-sm rounded-md border border-border px-da-sm py-[6px]"
            >
              <Icon name="check" size={14} className="shrink-0 text-muted-foreground" />
              <span className="w-4 shrink-0 text-center text-muted-foreground da-caption">
                {i + 1}
              </span>
              <StepText title={s.title} agent={s.agent_name ?? run.agent_name} />
              <span className="shrink-0 rounded-full bg-subtle px-da-sm py-[2px] text-foreground da-caption">
                Giữ kết quả
              </span>
            </li>
          ))}
          {steps.map((s, i) => {
            const tag = TAG[s.change];
            const label = s.title || 'bước mới';
            return (
              <li
                key={s.key}
                aria-label={`Bước ${label}`}
                onDragOver={(e) => dragging && e.preventDefault()}
                onDrop={() => dragging && move(dragging, i)}
                className={cn(
                  'flex min-h-[48px] items-center gap-da-sm rounded-md border px-da-sm py-[6px]',
                  s.change === 'edited' || s.change === 'added'
                    ? 'border-primary'
                    : 'border-border',
                  dragging === s.key && 'opacity-50',
                )}
              >
                <span
                  draggable
                  aria-hidden
                  onDragStart={() => setDragging(s.key)}
                  onDragEnd={() => setDragging(undefined)}
                  className="shrink-0 cursor-grab text-muted-foreground da-caption"
                >
                  ⠿
                </span>
                <span className="w-4 shrink-0 text-center text-muted-foreground da-caption">
                  {s.number ?? '+'}
                </span>
                {editing === s.key ? (
                  <div className="flex min-w-0 flex-1 items-center gap-da-sm">
                    <input
                      autoFocus
                      aria-label="Tên bước"
                      value={s.title}
                      onChange={(e) => update(s.key, { title: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') setEditing(undefined);
                      }}
                      className="h-8 min-w-0 flex-1 rounded-sm border border-border-strong bg-card px-da-sm text-foreground da-body-small"
                    />
                    <div className="w-[170px] shrink-0">
                      <Select
                        label="Agent làm bước"
                        value={s.agent_name}
                        onChange={(v) => update(s.key, { agent_name: v })}
                        options={agents.map((a) => ({ value: a, label: a }))}
                      />
                    </div>
                    <Button size="sm" onClick={() => setEditing(undefined)}>
                      Xong
                    </Button>
                  </div>
                ) : (
                  <StepText title={label} agent={s.agent_name} muted={s.change === 'removed'} />
                )}
                {tag && editing !== s.key && <StatusDot tone={tag.tone}>{tag.label}</StatusDot>}
                {editing !== s.key && (
                  <Menu
                    label={`Thao tác với bước ${label}`}
                    items={[
                      ...(s.change === 'removed'
                        ? [
                            {
                              label: 'Giữ lại bước',
                              onSelect: () => update(s.key, { change: s.id ? 'kept' : 'added' }),
                            },
                          ]
                        : [
                            {
                              label: 'Sửa bước',
                              icon: 'edit' as const,
                              onSelect: () => setEditing(s.key),
                            },
                          ]),
                      ...(i > 0 ? [{ label: 'Lên trên', onSelect: () => move(s.key, i - 1) }] : []),
                      ...(i < steps.length - 1
                        ? [{ label: 'Xuống dưới', onSelect: () => move(s.key, i + 1) }]
                        : []),
                      ...(s.change !== 'removed'
                        ? [
                            { kind: 'separator' as const },
                            s.id
                              ? {
                                  label: 'Bỏ bước',
                                  icon: 'trash' as const,
                                  danger: true,
                                  onSelect: () => update(s.key, { change: 'removed' }),
                                }
                              : {
                                  label: 'Xoá bước mới',
                                  icon: 'trash' as const,
                                  danger: true,
                                  onSelect: () =>
                                    setSteps((all) => all.filter((x) => x.key !== s.key)),
                                },
                          ]
                        : []),
                    ]}
                  />
                )}
              </li>
            );
          })}
        </ol>
        <div>
          <Button kind="ghost" icon="plus" onClick={add}>
            Thêm bước
          </Button>
        </div>
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Ghi chú cho agent</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder="Điều agent cần biết khi chạy tiếp, ví dụ nguồn không được dùng"
            className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
          />
        </label>
        {estimate && (
          <p
            role="status"
            className="flex items-center gap-da-sm rounded-md border border-border px-da-md py-da-sm text-foreground da-caption"
          >
            <Icon name="agent" size={16} className="shrink-0 text-info" />
            {estimate}
          </p>
        )}
      </div>
    </Drawer>
  );
}

function StepText({ title, agent, muted }: { title: string; agent: string; muted?: boolean }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <span
        className={cn(
          'truncate da-body-small',
          muted ? 'text-muted-foreground line-through' : 'text-foreground',
        )}
      >
        {title}
      </span>
      <span className="truncate text-muted-foreground da-caption">{agent}</span>
    </div>
  );
}
