import { useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { listSources } from '../../lib/v1/api';
import {
  createDataSpace,
  listAdminUsers,
  listGroups,
  type DefaultPolicy,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Quản trị · Tạo Data Space" (2232:15011, UC-11-08): four numbered sections in cards
 * (thông tin chung, nguồn, chính sách mặc định, thành viên) then "Tạo Data Space" / "Lưu nháp";
 * on the right, what happens after creation. The "Gói ngành" line of section 2 is not drawn:
 * there is no pack endpoint for it yet.
 */
const POLICY_FLAGS: { key: keyof DefaultPolicy; label: string }[] = [
  { key: 'mask_pii', label: 'Che dữ liệu cá nhân (CMND, số tài khoản, số điện thoại) cho mọi vai trò trừ Kiểm soát' },
  { key: 'agent_write_derived_only', label: 'Agent chỉ ghi bản derived; ghi ngược vào nguồn luôn cần duyệt' },
  { key: 'show_filed_and_current', label: 'Câu hỏi về kỳ đã nộp luôn hiện cả số đã nộp và số hiện tại' },
  { key: 'allow_external_channels', label: 'Cho phép gửi thẻ việc qua Teams, Zalo' },
];

const NEXT_STEPS = [
  { title: '1. Kết nối hoặc chọn nguồn', text: 'Chỉ đọc qua tài khoản kỹ thuật' },
  { title: '2. Ánh xạ gói ngành', text: 'Agent đề xuất, người phụ trách xác nhận theo lô' },
  { title: '3. Chạy bộ đánh giá', text: 'Đạt ngưỡng mới mở cho người dùng' },
  { title: '4. Mời thành viên', text: 'Theo nhóm AD hoặc từng người' },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-xl">
      <h2 className="text-foreground da-body-strong font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export default function CreateDataSpacePage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const users = useV1(listAdminUsers, []);
  const sources = useV1(listSources, []);
  const groups = useV1(listGroups, []);

  const [name, setName] = useState('');
  const [stewardId, setStewardId] = useState<string | null>(null);
  const [description, setDescription] = useState('');
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [policy, setPolicy] = useState<DefaultPolicy>({
    mask_pii: true,
    agent_write_derived_only: true,
    show_filed_and_current: true,
    allow_external_channels: false,
  });
  const [sending, setSending] = useState<'draft' | 'onboarding' | null>(null);
  const [error, setError] = useState<string>();

  const toggle = (list: string[], id: string) =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  const submit = async (status: 'draft' | 'onboarding') => {
    if (!name.trim() || !stewardId) {
      setError('Nhập tên và chọn người phụ trách dữ liệu.');
      return;
    }
    setSending(status);
    setError(undefined);
    try {
      const created = await createDataSpace({
        name: name.trim(),
        description: description.trim() || undefined,
        steward_id: stewardId,
        default_policy: policy,
        source_ids: sourceIds,
        group_ids: groupIds,
        status,
      });
      toast({
        tone: 'success',
        message: status === 'draft' ? `Đã lưu nháp ${created.name}` : `Đã tạo ${created.name}`,
      });
      navigate('admin', 'DATA_SPACES', created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(null);
    }
  };

  const people = (users.data?.items ?? []).filter((u) => u.kind === 'person');
  const chosenGroups = (groups.data?.items ?? []).filter((g) => groupIds.includes(g.id));

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 max-w-[840px] flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <PageHeader
          breadcrumb={['Quản trị', 'Data Space', 'Tạo mới']}
          title="Tạo Data Space"
          description="Data Space là vùng dữ liệu riêng: nguồn, chỉ số, agent và quyền không lẫn sang vùng khác. Câu hỏi và agent trong vùng này chỉ thấy những gì được khai báo ở đây."
        />
        <Section title="1. Thông tin chung">
          <div className="flex gap-da-lg max-[1023px]:flex-col">
            <div className="min-w-0 flex-1">
              <Field label="Tên">
                <Input
                  className="w-full"
                  value={name}
                  placeholder="Ví dụ: Rủi ro tín dụng"
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
            </div>
            <div className="min-w-0 flex-1">
              <Field label="Người phụ trách dữ liệu">
                <Select
                  label="Người phụ trách dữ liệu"
                  placeholder="Chọn người · duyệt thay đổi chỉ số và quyền"
                  options={people.map((u) => ({ value: u.id, label: u.display_name }))}
                  value={stewardId}
                  onChange={setStewardId}
                />
              </Field>
            </div>
          </div>
          <Field label="Mô tả">
            <Input
              className="w-full"
              value={description}
              placeholder="Dùng cho phân tích rủi ro tín dụng bán lẻ và doanh nghiệp"
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>
        </Section>
        <Section title="2. Nguồn và gói ngành">
          <AsyncRegion state={sources} what="nguồn dữ liệu">
            {(p) => (
              <div className="flex flex-wrap gap-da-sm" role="group" aria-label="Nguồn">
                {p.items.map((s) => {
                  const on = sourceIds.includes(s.id);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setSourceIds((list) => toggle(list, s.id))}
                      className="inline-flex items-center gap-da-sm rounded-md px-da-xs text-foreground da-body-small hover:bg-subtle"
                    >
                      <span
                        aria-hidden
                        className={cn('size-[7px] rounded-full', on ? 'bg-success' : 'bg-border-strong')}
                      />
                      {s.name}
                    </button>
                  );
                })}
              </div>
            )}
          </AsyncRegion>
        </Section>
        <Section title="3. Chính sách mặc định">
          <div className="flex flex-col gap-da-sm">
            {POLICY_FLAGS.map((flag) => (
              <Checkbox
                key={flag.key}
                checked={policy[flag.key]}
                onChange={(checked) => setPolicy((p) => ({ ...p, [flag.key]: checked }))}
              >
                {flag.label}
              </Checkbox>
            ))}
          </div>
        </Section>
        <Section title="4. Thành viên">
          <AsyncRegion state={groups} what="nhóm AD">
            {(p) => (
              <div className="flex flex-col gap-da-sm">
                <div className="flex flex-wrap gap-x-da-xl gap-y-da-sm">
                  {p.items.map((g) => (
                    <Checkbox
                      key={g.id}
                      checked={groupIds.includes(g.id)}
                      onChange={() => setGroupIds((list) => toggle(list, g.id))}
                    >
                      {g.name} · {g.member_count} người
                    </Checkbox>
                  ))}
                </div>
                {chosenGroups.length > 0 && (
                  <p className="text-foreground da-caption">
                    Đồng bộ từ nhóm AD:{' '}
                    {chosenGroups.map((g) => `${g.name} (${g.member_count} người)`).join(' · ')}
                  </p>
                )}
              </div>
            )}
          </AsyncRegion>
        </Section>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="flex gap-da-md">
          <Button kind="primary" loading={sending === 'onboarding'} onClick={() => submit('onboarding')}>
            Tạo Data Space
          </Button>
          <Button loading={sending === 'draft'} onClick={() => submit('draft')}>
            Lưu nháp
          </Button>
        </div>
      </div>
      <aside className="flex w-[380px] shrink-0 flex-col gap-da-lg border-l border-border bg-background px-da-xl py-da-xl max-[1279px]:hidden">
        <h2 className="text-muted-foreground da-caption">Sau khi tạo</h2>
        {NEXT_STEPS.map((step) => (
          <div key={step.title} className="flex flex-col gap-[2px] rounded-md border border-border bg-card px-[11px] py-[11px]">
            <span className="text-foreground da-body-strong">{step.title}</span>
            <span className="text-muted-foreground da-caption">{step.text}</span>
          </div>
        ))}
        <p className="text-muted-foreground da-caption">
          Tạo Data Space cần vai trò Quản trị hệ thống và được ghi kiểm toán.
        </p>
      </aside>
    </div>
  );
}
