import { useEffect, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  approveSkillVersion,
  getSkill,
  getSkillVersionReview,
  listSkills,
  listSkillVersions,
  requestSkillChanges,
  type Skill,
  type SkillVersion,
  type SkillVersionReview,
} from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';
import { VERSION_TONE } from './labels';

/*
 * "P03 · Phiên bản kỹ năng · Duyệt & ngừng" (2294:18535, UC-03-05, UC-03-06): the versions of
 * one skill, and for the version waiting for review the checks before approval, the scope to
 * publish to, when the version in use stops and who still runs it. Agents never upgrade on
 * their own: publishing asks each agent owner to approve the upgrade ("Xác nhận · publishSkill").
 * Opened from the rail ("Đang chờ duyệt") without an id, it shows the first skill waiting.
 */
interface Loaded {
  skill: Skill;
  versions: SkillVersion[];
  review: SkillVersionReview | null;
}

async function load(routeId: string | undefined): Promise<Loaded | null> {
  let id = routeId;
  if (!id) {
    const waiting = (await listSkills()).items.find((s) => s.pending_version_id);
    if (!waiting) return null;
    id = waiting.id;
  }
  const [skill, versions] = await Promise.all([getSkill(id), listSkillVersions(id)]);
  const review = skill.pending_version_id
    ? await getSkillVersionReview(skill.pending_version_id)
    : null;
  return { skill, versions: versions.items, review };
}

export default function SkillVersionsPage() {
  const { routeId } = useAppState();
  // Opened from the rail, the skill found first stays on screen after it is published.
  const shown = useRef<string | undefined>(undefined);
  const state = useV1(
    () =>
      load(routeId || shown.current).then((data) => {
        shown.current = data?.skill.id;
        return data;
      }),
    [routeId],
  );
  return (
    <AsyncRegion
      state={state}
      what="phiên bản kỹ năng"
      skeletonRows={5}
      isEmpty={(data) => data === null}
      empty={
        <div className="px-da-3xl pt-da-xl">
          <EmptyState
            icon="check"
            title="Không có phiên bản nào chờ duyệt"
            description="Khi một kỹ năng được gửi duyệt, phiên bản đó hiện ở đây cùng kết quả kiểm định."
          />
        </div>
      }
    >
      {(data) => data && <Versions data={data} reload={state.reload} />}
    </AsyncRegion>
  );
}

function Versions({ data, reload }: { data: Loaded; reload: () => void }) {
  const toast = useToast();
  const { skill, versions, review } = data;
  const pending = versions.find((v) => v.id === review?.version_id);
  const current = review?.current;

  const [scope, setScope] = useState(review?.scope_value ?? null);
  const [stopAt, setStopAt] = useState(review?.deprecate_options[0]?.value ?? null);
  const [replacement, setReplacement] = useState(review?.replacement_options[0]?.value ?? null);
  const [dialog, setDialog] = useState<'publish' | 'changes' | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setScope(review?.scope_value ?? null);
    setStopAt(review?.deprecate_options[0]?.value ?? null);
    setReplacement(review?.replacement_options[0]?.value ?? null);
  }, [review]);

  const open = (which: 'publish' | 'changes') => {
    setError(undefined);
    setNote('');
    setDialog(which);
  };

  const act = (call: () => Promise<unknown>, done: string) => {
    setBusy(true);
    call()
      .then(() => {
        setDialog(null);
        toast({ tone: 'success', message: done });
        reload();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const active = versions.find((v) => v.status === 'active');
  const summary = [
    `${versions.length} phiên bản`,
    pending && `v${pending.version} chờ duyệt`,
    current
      ? `v${current.version} đang dùng ở ${current.agent_count} agent và ${current.session_count} phiên`
      : active && `v${active.version} đang dùng`,
  ]
    .filter(Boolean)
    .join(' · ');

  const blocked = review && !review.can_approve.allowed ? review.can_approve.reason : null;
  const publishButton = pending && (
    <Button kind="primary" disabled={Boolean(blocked)} onClick={() => open('publish')}>
      Duyệt và phát hành v{pending.version}
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', skill.name]}
        title={
          <span className="flex items-center gap-[10px]">
            {skill.name}
            {pending && (
              <StatusDot tone="warning" className="font-normal">
                v{pending.version} chờ duyệt
              </StatusDot>
            )}
          </span>
        }
        description={summary}
        actions={
          pending && (
            <>
              <Button kind="ghost" onClick={() => open('changes')}>
                Yêu cầu sửa
              </Button>
              {blocked ? <Tooltip content={blocked}>{publishButton}</Tooltip> : publishButton}
            </>
          )
        }
      />
      <div className="flex flex-1">
        <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
          <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Phiên bản</h2>
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="w-[72px] py-da-md pl-da-lg font-normal">Bản</th>
                  <th className="py-da-md pl-da-lg font-normal">Thay đổi</th>
                  <th className="w-[132px] py-da-md pl-da-lg font-normal">Kiểm định</th>
                  <th className="w-[214px] px-da-lg py-da-md font-normal">Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id} className="border-b border-border">
                    <td className="py-[9px] pl-da-lg text-foreground da-mono">v{v.version}</td>
                    <td className="py-[9px] pl-da-lg text-foreground da-body-small">
                      {v.change_text}
                    </td>
                    <td className="py-[9px] pl-da-lg text-muted-foreground da-body-small">
                      {v.eval_total > 0 ? `${v.eval_passed}/${v.eval_total} đạt` : '–'}
                    </td>
                    <td className="px-da-lg py-[9px]">
                      <StatusDot tone={VERSION_TONE[v.status]}>{v.status_text}</StatusDot>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {review && pending && current && (
            <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
              <h2 className="text-foreground da-heading-3">
                Khi phát hành v{pending.version}, ngừng dùng v{current.version}?
              </h2>
              <div className="grid grid-cols-2 gap-da-lg">
                <Field label={`Ngừng v${current.version} từ`} labelTone="muted">
                  <Select
                    label={`Ngừng v${current.version} từ`}
                    options={review.deprecate_options}
                    value={stopAt}
                    onChange={setStopAt}
                  />
                </Field>
                <Field label="Chuyển người dùng sang" labelTone="muted">
                  <Select
                    label="Chuyển người dùng sang"
                    options={review.replacement_options}
                    value={replacement}
                    onChange={setReplacement}
                  />
                </Field>
              </div>
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="py-da-md pl-da-lg font-normal">Đang dùng v{current.version}</th>
                    <th className="w-[112px] py-da-md pl-da-lg font-normal">Loại</th>
                    <th className="w-[324px] px-da-lg py-da-md font-normal">Việc cần làm</th>
                  </tr>
                </thead>
                <tbody>
                  {review.consumers.map((c) => (
                    <tr key={c.name} className="border-b border-border">
                      <td className="py-[9px] pl-da-lg text-foreground da-body-strong">{c.name}</td>
                      <td className="py-[9px] pl-da-lg text-muted-foreground da-body-small">
                        {c.kind === 'agent' ? 'Agent' : 'Phiên'}
                      </td>
                      <td className="px-da-lg py-[9px] text-foreground da-body-small">
                        {c.action_text}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>

        {review && (
          <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pb-da-xl pt-[20px] max-[1279px]:hidden">
            <h2 className="text-foreground da-heading-3">Kiểm tra trước khi duyệt</h2>
            <ul className="flex flex-col gap-[10px]">
              {review.checks.map((c) => (
                <li
                  key={c.text}
                  className="flex items-center gap-da-md text-foreground da-body-small"
                >
                  <Icon
                    name={c.ok ? 'check' : 'alert'}
                    size={14}
                    className={c.ok ? 'text-success' : 'text-error'}
                  />
                  <span>
                    {c.text}
                    {!c.ok && <span className="sr-only"> (chưa đạt)</span>}
                  </span>
                </li>
              ))}
            </ul>
            <h2 className="text-foreground da-heading-3">Phạm vi dùng</h2>
            <Select
              label="Phạm vi dùng"
              options={review.scope_options}
              value={scope}
              onChange={setScope}
            />
            <p className="text-foreground da-body-small">
              Agent không tự nâng phiên bản kỹ năng; chủ sở hữu từng agent nhận yêu cầu duyệt. Kết
              quả cũ luôn ghi rõ phiên bản đã dùng.
            </p>
          </aside>
        )}
      </div>

      {pending && (
        <>
          <ConfirmDialog
            open={dialog === 'publish'}
            tone="primary"
            title={`Xuất bản kỹ năng v${pending.version}?`}
            confirmLabel="Xuất bản"
            loading={busy}
            error={error}
            onCancel={() => setDialog(null)}
            onConfirm={() =>
              act(
                () =>
                  approveSkillVersion(pending.id, {
                    scope: scope ?? '',
                    deprecate_previous: stopAt ?? '',
                    replacement: replacement ?? '',
                  }),
                `Đã phát hành ${skill.name} v${pending.version}`,
              )
            }
          >
            {current
              ? `${current.agent_count} agent đang dùng v${current.version} không tự nâng cấp; chủ sở hữu từng agent sẽ được thông báo.`
              : 'Phiên bản đầu tiên: agent chỉ dùng khi chủ sở hữu gán kỹ năng.'}
          </ConfirmDialog>
          <ConfirmDialog
            open={dialog === 'changes'}
            size="md"
            tone="primary"
            title="Yêu cầu sửa"
            confirmLabel="Gửi yêu cầu"
            loading={busy}
            error={error}
            onCancel={() => setDialog(null)}
            onConfirm={() => {
              if (!note.trim()) {
                setError('Ghi điều cần sửa.');
                return;
              }
              act(
                () => requestSkillChanges(pending.id, note.trim()),
                `Đã trả v${pending.version} về ${skill.created_by.name}`,
              );
            }}
          >
            <label className="flex flex-col gap-da-xs">
              <span>
                Phiên bản v{pending.version} trở về {skill.created_by.name} kèm nhận xét của bạn.
              </span>
              <Textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                aria-label="Điều cần sửa"
                className="w-full"
              />
            </label>
          </ConfirmDialog>
        </>
      )}
    </div>
  );
}
