import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
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
 * one skill, and for the version waiting for review the checks before approval (its eval run) and
 * the scope it is published to. Approving or asking for changes decides the skill_publish
 * proposal in Cần bạn; the version in use retires after the 30-day transition (BR-97). Agents
 * never upgrade on their own ("Xác nhận · publishSkill").
 * Opened from the rail ("Đang chờ duyệt") without an id, it shows the first skill waiting.
 */
interface Loaded {
  skill: Skill;
  versions: SkillVersion[];
  review: SkillVersionReview | null;
}

/** The list row omits versions, so a skill waiting for review is found on its detail. */
async function waitingSkillId(): Promise<string | undefined> {
  const items = (await listSkills()).items;
  const listed = items.find((skill) => skill.pending_version_id);
  if (listed) return listed.id;
  for (const skill of items) {
    const detail = await getSkill(skill.id).catch(() => null);
    if (detail?.pending_version_id) return detail.id;
  }
  return undefined;
}

async function load(routeId: string | undefined): Promise<Loaded | null> {
  let id = routeId;
  if (!id) {
    id = await waitingSkillId();
    if (!id) return null;
  }
  const [skill, versions] = await Promise.all([getSkill(id), listSkillVersions(id)]);
  const review = skill.pending_version_id
    ? await getSkillVersionReview(id, skill.pending_version_id)
    : null;
  return { skill, versions: versions.items, review };
}

export default function SkillVersionsPage() {
  const { routeId } = useAppState();
  // Opened from the rail, the skill found first stays on screen after it is published.
  const shown = useRef<string | undefined>(undefined);
  const state = useV1(['SkillVersionsPage.load', routeId], () =>
    load(routeId || shown.current).then((data) => {
      shown.current = data?.skill.id;
      return data;
    }),
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

  const [dialog, setDialog] = useState<'publish' | 'changes' | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

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
  const view = useTableView(versions, {
    search: (v) => `${v.version} ${v.change_text} ${v.status_text}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (v) => v.status_text }],
    sorts: [
      { id: 'version', value: (v) => v.version },
      { id: 'change', value: (v) => v.change_text },
      { id: 'eval', value: (v) => (v.eval_total > 0 ? v.eval_passed / v.eval_total : null) },
      { id: 'status', value: (v) => v.status_text },
    ],
    pageSize: 50,
  });
  const summary = [
    `${versions.length} phiên bản`,
    pending && `v${pending.version} chờ duyệt`,
    active && `v${active.version} đang dùng`,
  ]
    .filter(Boolean)
    .join(' · ');

  const blocked = review && !review.can_approve.allowed ? review.can_approve.reason : null;
  const proposalId = review?.proposal_id ?? null;
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
              <Button kind="ghost" disabled={Boolean(blocked)} onClick={() => open('changes')}>
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
            <TableToolbar view={view} placeholder="Tìm phiên bản…" searchLabel="Tìm phiên bản" />
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh
                    view={view}
                    column="version"
                    className="w-[72px] py-da-md pl-da-lg font-normal"
                  >
                    Bản
                  </SortableTh>
                  <SortableTh view={view} column="change" className="py-da-md pl-da-lg font-normal">
                    Thay đổi
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="eval"
                    className="w-[132px] py-da-md pl-da-lg font-normal"
                  >
                    Kiểm định
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="status"
                    className="w-[214px] px-da-lg py-da-md font-normal"
                  >
                    Trạng thái
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((v) => (
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
            <TablePager view={view} />
          </section>

          {review && pending && current && (
            <p className="py-da-xl text-foreground da-body-small">
              Khi phát hành v{pending.version}, v{current.version} ngừng dùng sau 30 ngày; người
              dùng chuyển sang v{pending.version} với tham số giữ nguyên. Kết quả cũ luôn ghi rõ
              phiên bản đã dùng.
            </p>
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
            <p className="text-foreground da-body-small">{review.scope?.label ?? '—'}</p>
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
              proposalId &&
              act(
                () => approveSkillVersion(proposalId),
                `Đã phát hành ${skill.name} v${pending.version}`,
              )
            }
          >
            {current
              ? `Agent đang dùng v${current.version} không tự nâng cấp; chủ sở hữu từng agent sẽ được thông báo.`
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
              if (!proposalId) return;
              act(
                () => requestSkillChanges(proposalId, note.trim()),
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
