import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Menu } from '../../design-system/Menu';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { createSkill, listSkills, type Skill, type SkillScope } from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';
import { SKILL_TONE, runsText } from './labels';
import { ShareSkillDialog } from './ShareSkillDialog';
import { Segmented } from '../../design-system/Segmented';

/*
 * "P03 · Kỹ năng · Thư viện" (2293:17995, UC-03-01, UC-03-07): every skill the viewer can use,
 * filtered by where it comes from. The name opens "Chạy kỹ năng" (skill-authoring); the row
 * menu edits it, opens its versions or shares a certified one.
 * "Tạo từ phiên" starts from a notebook ("Biến thành kỹ năng" lives there), so it opens the
 * session list; "Kỹ năng mới" creates an empty draft and opens it in the editor.
 */
type Filter = 'all' | SkillScope;

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Tất cả' },
  { value: 'data_space', label: 'Của Data Space' },
  { value: 'organization', label: 'Toàn tổ chức' },
  { value: 'domain_pack', label: 'Từ gói ngành' },
];

export default function SkillLibraryPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const skills = useV1(['listSkills'], () => listSkills());
  const [filter, setFilter] = useState<Filter>('all');
  const [creating, setCreating] = useState(false);
  const [sharing, setSharing] = useState<Skill | null>(null);
  const items = skills.data?.items ?? [];
  const count = (f: Filter) => items.filter((s) => f === 'all' || s.scope === f).length;
  const view = useTableView(items, {
    search: (s) =>
      `${s.name} ${s.description} ${s.scope_label} ${s.status_text} ${s.owner.name} v${s.version}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (s) => s.status_text }],
    predicate: (s) => filter === 'all' || s.scope === filter,
    sorts: [
      { id: 'name', value: (s) => s.name },
      { id: 'version', value: (s) => s.version },
      { id: 'scope', value: (s) => s.scope_label },
      { id: 'status', value: (s) => s.status_text },
      { id: 'runs', value: (s) => s.runs_30d },
      { id: 'owner', value: (s) => s.owner.name },
    ],
    pageSize: 50,
  });

  const create = () => {
    setCreating(true);
    createSkill({ name: 'Kỹ năng chưa đặt tên' })
      .then((skill) => navigate('agents', 'SKILL_EDITOR', skill.id))
      .catch((e: Error) => toast({ tone: 'error', message: `Không tạo được: ${e.message}` }))
      .finally(() => setCreating(false));
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng']}
        title="Thư viện kỹ năng"
        description="Kỹ năng là cách làm phân tích có tham số, dùng lại được bởi người và agent · chỉ gọi chỉ số qua lớp ngữ cảnh nên không phụ thuộc cấu trúc bảng"
        actions={
          <>
            <Button onClick={() => navigate('analysis', 'SESSIONS')}>Tạo từ phiên</Button>
            <Button kind="primary" loading={creating} onClick={create}>
              Kỹ năng mới
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        <TableToolbar view={view} placeholder="Tìm kỹ năng…" searchLabel="Tìm kỹ năng">
          <Segmented<Filter>
            variant="chips"
            label="Nguồn kỹ năng"
            items={FILTERS.map((f) => ({
              value: f.value,
              label: skills.data ? `${f.label} ${count(f.value)}` : f.label,
            }))}
            value={filter}
            onChange={setFilter}
          />
        </TableToolbar>
        <AsyncRegion
          state={skills}
          what="thư viện kỹ năng"
          skeletonRows={5}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="layers"
              title="Chưa có kỹ năng nào"
              description="Biến một phiên phân tích thành kỹ năng để người và agent dùng lại."
            />
          }
        >
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh view={view} column="name" className="py-da-md pl-da-lg font-normal">
                    Kỹ năng
                  </SortableTh>
                  <SortableTh view={view} column="version" className="w-[102px] py-da-md pl-da-lg font-normal">
                    Phiên bản
                  </SortableTh>
                  <SortableTh view={view} column="scope" className="w-[162px] py-da-md pl-da-lg font-normal">
                    Phạm vi
                  </SortableTh>
                  <SortableTh view={view} column="status" className="w-[182px] py-da-md pl-da-lg font-normal">
                    Trạng thái
                  </SortableTh>
                  <SortableTh view={view} column="runs" className="w-[102px] py-da-md pl-da-lg font-normal">
                    30 ngày
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="owner"
                    className="w-[152px] py-da-md pl-da-lg font-normal max-[1279px]:hidden"
                  >
                    Chủ sở hữu
                  </SortableTh>
                  <SortableTh view={view} className="w-[40px] py-da-md pl-da-lg pr-da-lg">
                    <span className="sr-only">Thao tác</span>
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((skill) => (
                    <SkillRow
                      key={skill.id}
                      skill={skill}
                      onRun={() => navigate('agents', 'SKILL_RUN', skill.id)}
                      onEdit={() => navigate('agents', 'SKILL_EDITOR', skill.id)}
                      onVersions={() => navigate('agents', 'SKILL_VERSIONS', skill.id)}
                      onShare={() => setSharing(skill)}
                    />
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <TablePager view={view} />
      </div>
      <ShareSkillDialog skill={sharing} onClose={() => setSharing(null)} />
    </div>
  );
}

function SkillRow({
  skill,
  onRun,
  onEdit,
  onVersions,
  onShare,
}: {
  skill: Skill;
  onRun: () => void;
  onEdit: () => void;
  onVersions: () => void;
  onShare: () => void;
}) {
  return (
    <tr className="border-b border-border align-middle">
      <td className="py-[9px] pl-da-lg">
        <div className="flex min-w-0 flex-col items-start gap-[2px]">
          <button
            type="button"
            onClick={onRun}
            className="text-left text-foreground da-body-strong hover:underline"
          >
            {skill.name}
          </button>
          <span className="text-muted-foreground da-caption">{skill.description}</span>
        </div>
      </td>
      <td className="py-[9px] pl-da-lg text-foreground da-mono">v{skill.version}</td>
      <td className="py-[9px] pl-da-lg text-foreground da-body-small">{skill.scope_label}</td>
      <td className="py-[9px] pl-da-lg">
        <StatusDot tone={SKILL_TONE[skill.status]}>{skill.status_text}</StatusDot>
      </td>
      <td className="py-[9px] pl-da-lg text-muted-foreground da-mono">
        {runsText(skill.runs_30d)}
      </td>
      <td className="py-[9px] pl-da-lg text-muted-foreground da-body-small max-[1279px]:hidden">
        {skill.owner.name}
      </td>
      <td className="py-[9px] pl-da-lg pr-da-lg text-right">
        <Menu
          label={`Thao tác với ${skill.name}`}
          items={[
            { label: 'Chạy', icon: 'play', onSelect: onRun },
            { label: 'Sửa', icon: 'edit', onSelect: onEdit },
            { label: 'Phiên bản', icon: 'layers', onSelect: onVersions },
            ...(skill.status === 'certified'
              ? [{ label: 'Chia sẻ', icon: 'users' as const, onSelect: onShare }]
              : []),
          ]}
        />
      </td>
    </tr>
  );
}
