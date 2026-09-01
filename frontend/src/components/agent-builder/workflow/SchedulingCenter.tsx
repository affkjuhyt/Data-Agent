/**
 * Scheduling Center — schedule definitions persisted as a platform setting.
 *
 *   GET/PUT /api/platform/settings/schedules  → { value: Schedule[] }
 *   GET /api/agents, GET /api/agents/{id}/workflows (workflow picker)
 *
 * CRUD is real (every change PUTs the whole array). There is no scheduler
 * service yet, so nothing here triggers a run — the banner says so.
 */
import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, CalendarClock, CheckCircle2, Clock, Mail, MessageSquare, Pencil, Play, Plus, RefreshCw, Search, Trash2, Power } from 'lucide-react';
import { cn } from '../../../lib/utils';
import {
  runDueSchedules,
  runRoiUseCase,
  usePlatformSetting,
  useAgents,
  useApi,
  useRoiUseCases,
  type DeliveryChannel,
  type Schedule,
  type AgentWorkflow,
} from '../../../lib/platformApi';
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState, Select, SectionTitle, Table, type Column,
} from '../../ui';
import { formatDateTime } from '../../../lib/formatVN';

const newId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `sc-${Date.now()}`;

interface FormState {
  id: string | null;
  name: string;
  agent_id: string;
  workflow_id: string;
  cron: string;
  enabled: boolean;
  report_kind: 'weekly_report' | 'data_quality' | 'anomaly' | 'forecast';
}

const EMPTY_FORM: FormState = { id: null, name: '', agent_id: '', workflow_id: '', cron: '0 9 * * 1-5', enabled: true, report_kind: 'weekly_report' };
const REPORT_LABELS: Record<FormState['report_kind'], string> = {
  weekly_report: 'Narrative tuần',
  data_quality: 'Data quality',
  anomaly: 'Bất thường',
  forecast: 'Dự báo',
};

function ScheduleForm({ initial, saving, onCancel, onSubmit }: {
  initial: FormState; saving: boolean; onCancel: () => void; onSubmit: (f: FormState, workflowName: string | null) => void;
}) {
  const [form, setForm] = useState<FormState>(initial);
  const agents = useAgents();
  const workflows = useApi<AgentWorkflow[]>(form.agent_id ? `/api/agents/${form.agent_id}/workflows` : null);

  useEffect(() => { setForm(initial); }, [initial]);

  const wfList = workflows.data ?? [];
  const selectedWf = wfList.find(w => w.id === form.workflow_id) ?? null;
  const valid = form.name.trim().length > 0 && form.cron.trim().split(/\s+/).length >= 5;

  return (
    <form
      className="space-y-4"
      onSubmit={e => { e.preventDefault(); if (valid) onSubmit(form, selectedWf?.name ?? null); }}
    >
      <Field label="Tên lịch">
        <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="VD: Đồng bộ KB hằng ngày" autoFocus />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Agent" hint={agents.error ? 'Không tải được danh sách agent' : undefined}>
          <Select value={form.agent_id} onChange={e => setForm(f => ({ ...f, agent_id: e.target.value, workflow_id: '' }))}>
            <option value="">— Chọn agent —</option>
            {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </Field>
        <Field label="Workflow" hint={form.agent_id && !workflows.loading && wfList.length === 0 ? 'Agent này chưa có workflow' : undefined}>
          <Select value={form.workflow_id} onChange={e => setForm(f => ({ ...f, workflow_id: e.target.value }))} disabled={!form.agent_id || workflows.loading}>
            <option value="">{workflows.loading ? 'Đang tải…' : '— Chọn workflow —'}</option>
            {wfList.map(w => <option key={w.id} value={w.id}>{w.name}{w.published_version_id ? '' : ' (chưa publish)'}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Cron" hint="5 trường: phút giờ ngày tháng thứ (giờ máy chủ). VD: 0 */4 * * * = mỗi 4 giờ.">
        <Input value={form.cron} onChange={e => setForm(f => ({ ...f, cron: e.target.value }))} className="font-mono" placeholder="0 9 * * 1-5" />
      </Field>
      <Field label="Loại job">
        <Select value={form.report_kind} onChange={e => setForm(f => ({ ...f, report_kind: e.target.value as FormState['report_kind'] }))}>
          {Object.entries(REPORT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </Select>
      </Field>
      <label className="flex items-center gap-2 text-[12px] text-[#111111]">
        <input type="checkbox" checked={form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} className="accent-[#B88719]" />
        Bật lịch này
      </label>
      <div className="flex justify-end gap-2 pt-2 border-t border-[#BFA66A]">
        <Button variant="ghost" onClick={onCancel} disabled={saving}>Huỷ</Button>
        <Button type="submit" variant="primary" loading={saving} disabled={!valid}>{form.id ? 'Lưu thay đổi' : 'Tạo lịch'}</Button>
      </div>
    </form>
  );
}

const SchedulingCenter = () => {
  const setting = usePlatformSetting<Schedule[]>('schedules', []);
  const delivery = usePlatformSetting<DeliveryChannel[]>('delivery_channels', []);
  const roi = useRoiUseCases();
  const schedules = Array.isArray(setting.value) ? setting.value : [];
  const channels = Array.isArray(delivery.value) ? delivery.value : [];
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<FormState | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [channelKind, setChannelKind] = useState<DeliveryChannel['kind']>('slack');
  const [channelTarget, setChannelTarget] = useState('');
  const [channelWebhook, setChannelWebhook] = useState('');

  const persist = async (next: Schedule[]) => {
    setActionError(null);
    try {
      await setting.save(next);
      return true;
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Lưu thất bại');
      return false;
    }
  };

  const handleSubmit = async (f: FormState, workflowName: string | null) => {
    const entry: Schedule = {
      id: f.id ?? newId(),
      name: f.name.trim(),
      workflow_id: f.workflow_id || null,
      cron: f.cron.trim(),
      enabled: f.enabled,
      report_kind: f.report_kind,
      last_run: f.id ? (schedules.find(s => s.id === f.id)?.last_run ?? null) : null,
      agent_id: f.agent_id || null,
      workflow_name: workflowName,
    };
    const next = f.id ? schedules.map(s => (s.id === f.id ? entry : s)) : [...schedules, entry];
    if (await persist(next)) setEditing(null);
  };

  const toggle = (s: Schedule) => persist(schedules.map(x => (x.id === s.id ? { ...x, enabled: !x.enabled } : x)));
  const remove = (s: Schedule) => {
    if (!window.confirm(`Xoá lịch "${s.name}"?`)) return;
    void persist(schedules.filter(x => x.id !== s.id));
  };
  const edit = (s: Schedule) => setEditing({
    id: s.id, name: s.name, agent_id: s.agent_id ?? '', workflow_id: s.workflow_id ?? '', cron: s.cron, enabled: s.enabled,
    report_kind: s.report_kind ?? 'weekly_report',
  });
  const addChannel = async () => {
    const target = channelTarget.trim();
    if (!target) return;
    const next: DeliveryChannel = {
      id: newId(),
      kind: channelKind,
      target,
      webhook_url: channelKind === 'slack' ? channelWebhook.trim() || undefined : undefined,
      enabled: true,
    };
    try {
      await delivery.save([...channels, next]);
      setChannelTarget('');
      setChannelWebhook('');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Lưu kênh gửi thất bại');
    }
  };
  const toggleChannel = (id: string) => {
    void delivery.save(channels.map(c => (c.id === id ? { ...c, enabled: !c.enabled } : c)));
  };
  const removeChannel = (id: string) => {
    void delivery.save(channels.filter(c => c.id !== id));
  };
  const runNow = async (kind: string) => {
    setRunning(kind);
    setActionError(null);
    try {
      await runRoiUseCase(kind);
      await Promise.all([roi.refetch(), setting.refetch(), delivery.refetch()]);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Chạy use case thất bại');
    } finally {
      setRunning(null);
    }
  };
  const runSchedulesNow = async () => {
    setRunning('schedules');
    setActionError(null);
    try {
      await runDueSchedules();
      await Promise.all([roi.refetch(), setting.refetch()]);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Chạy scheduler thất bại');
    } finally {
      setRunning(null);
    }
  };

  const q = query.trim().toLowerCase();
  const visible = schedules.filter(s => !q || `${s.name} ${s.cron} ${s.workflow_name ?? ''}`.toLowerCase().includes(q));
  const enabledCount = schedules.filter(s => s.enabled).length;

  const columns: Column<Schedule>[] = [
    {
      header: 'Lịch',
      cell: s => (
        <div>
          <div className="font-semibold text-[#111111]">{s.name}</div>
          <div className="text-[11px] text-[#5F5F5F]">{s.workflow_name ?? (s.workflow_id ? s.workflow_id : 'Chưa gắn workflow')}</div>
        </div>
      ),
    },
    { header: 'Cron', cell: s => <code className="font-mono text-[12px] text-[#111111]">{s.cron}</code> },
    { header: 'Job', cell: s => <span className="text-[#3F3F3F]">{REPORT_LABELS[s.report_kind ?? 'weekly_report']}</span> },
    { header: 'Trạng thái', cell: s => <Badge tone={s.enabled ? 'healthy' : 'draft'}>{s.enabled ? 'Bật' : 'Tắt'}</Badge> },
    { header: 'Lần chạy cuối', cell: s => <span className="text-[#3F3F3F]">{s.last_run ? formatDateTime(s.last_run) : '—'}</span> },
    {
      header: '',
      align: 'right',
      cell: s => (
        <div className="flex items-center justify-end gap-1">
          <Button size="sm" variant="ghost" icon={Power} onClick={() => void toggle(s)} disabled={setting.saving} aria-label={s.enabled ? 'Tắt lịch' : 'Bật lịch'}>
            {s.enabled ? 'Tắt' : 'Bật'}
          </Button>
          <Button size="sm" variant="ghost" icon={Pencil} onClick={() => edit(s)} disabled={setting.saving}>Sửa</Button>
          <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(s)} disabled={setting.saving} aria-label="Xoá lịch" />
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Scheduling"
        subtitle="Lịch chạy workflow, report định kỳ, DQ, anomaly và forecast theo cron."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="ghost" icon={RefreshCw} onClick={() => void roi.refetch()} loading={roi.loading}>Refresh</Button>
            <Button variant="ghost" icon={Play} onClick={() => void runSchedulesNow()} loading={running === 'schedules'}>Chạy scheduler</Button>
            <Button variant="primary" icon={Plus} onClick={() => setEditing({ ...EMPTY_FORM })}>Lịch mới</Button>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-3">
        {(roi.data?.use_cases ?? []).map(item => (
          <Card key={item.id} className="min-h-[118px]">
            <div className="flex items-start justify-between gap-2">
              <div className="p-2 rounded-lg bg-[#F5E8B8] border border-[#D8C48A]">
                {item.id === 'data_quality' ? <CheckCircle2 className="w-4 h-4 text-[#1D6F4C]" /> : item.id === 'anomaly' ? <Activity className="w-4 h-4 text-[#A83A32]" /> : <BarChart3 className="w-4 h-4 text-[#8A5A00]" />}
              </div>
              <Badge tone={item.status === 'ready' || item.status === 'healthy' || item.status === 'clear' ? 'healthy' : item.status === 'needs_setup' ? 'draft' : 'warning'}>
                {item.status}
              </Badge>
            </div>
            <div className="mt-3 text-[13px] font-bold text-[#111111]">{item.name}</div>
            <div className="mt-1 text-[11px] text-[#5F5F5F] leading-relaxed">{item.evidence}</div>
            {['data_quality', 'anomaly', 'forecast'].includes(item.id) && (
              <button type="button" className="ab-link mt-2" onClick={() => void runNow(item.id === 'data_quality' ? 'data_quality' : item.id)} disabled={!!running}>
                <Play size={10} /> Chạy ngay
              </button>
            )}
          </Card>
        ))}
        {!roi.loading && !roi.data && (
          <Card className="xl:col-span-5">
            <ErrorState message={roi.error ?? 'Không tải được ROI use cases'} onRetry={() => void roi.refetch()} />
          </Card>
        )}
      </div>

      <Card title="Kênh gửi" subtitle="Slack webhook và email target được lưu tenant-scoped. Slack sẽ gửi ngay nếu có webhook; email được đưa vào outbox.">
        <div className="grid grid-cols-1 lg:grid-cols-[160px_1fr_1fr_auto] gap-2">
          <Select value={channelKind} onChange={e => setChannelKind(e.target.value as DeliveryChannel['kind'])}>
            <option value="slack">Slack</option>
            <option value="email">Email</option>
          </Select>
          <Input value={channelTarget} onChange={e => setChannelTarget(e.target.value)} placeholder={channelKind === 'slack' ? '#data-alerts' : 'data-team@example.com'} />
          <Input value={channelWebhook} onChange={e => setChannelWebhook(e.target.value)} placeholder="Slack webhook URL (tuỳ chọn)" disabled={channelKind !== 'slack'} />
          <Button variant="primary" icon={Plus} onClick={() => void addChannel()} loading={delivery.saving}>Thêm kênh</Button>
        </div>
        <div className="mt-3 divide-y divide-[#ECE7DA] border border-[#ECE7DA] rounded-xl overflow-hidden">
          {channels.length === 0 && <div className="p-3 text-[12px] text-[#5F5F5F]">Chưa có kênh gửi.</div>}
          {channels.map(c => (
            <div key={c.id} className="flex items-center gap-3 p-3 bg-white">
              {c.kind === 'slack' ? <MessageSquare className="w-4 h-4 text-[#4A5FD8]" /> : <Mail className="w-4 h-4 text-[#1D6F4C]" />}
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-semibold text-[#111111] truncate">{c.target}</div>
                <div className="text-[10px] uppercase tracking-wide text-[#5F5F5F]">{c.kind}</div>
              </div>
              <Badge tone={c.enabled ? 'healthy' : 'draft'}>{c.enabled ? 'Bật' : 'Tắt'}</Badge>
              <Button size="sm" variant="ghost" icon={Power} onClick={() => toggleChannel(c.id)}>{c.enabled ? 'Tắt' : 'Bật'}</Button>
              <Button size="sm" variant="danger" icon={Trash2} onClick={() => removeChannel(c.id)} aria-label="Xoá kênh" />
            </div>
          ))}
        </div>
      </Card>

      {editing && (
        <Card title={editing.id ? 'Sửa lịch' : 'Lịch mới'} subtitle="Cron 5 trường, giờ máy chủ">
          <ScheduleForm initial={editing} saving={setting.saving} onCancel={() => setEditing(null)} onSubmit={handleSubmit} />
        </Card>
      )}

      {actionError && <ErrorState message={actionError} />}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
          <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm lịch, cron, workflow…" className="pl-9 w-72" aria-label="Tìm lịch" />
        </div>
        <span className={cn('text-xs ml-auto', 'text-[#5F5F5F]')}>
          {schedules.length} lịch · {enabledCount} đang bật{setting.updatedAt ? ` · cập nhật ${formatDateTime(setting.updatedAt)}` : ''}
        </span>
      </div>

      <div className="content-card content-card--static overflow-hidden">
        {setting.loading && schedules.length === 0 && <LoadingState label="Đang tải lịch…" />}
        {setting.error && <div className="p-4"><ErrorState message={setting.error} onRetry={() => void setting.refetch()} /></div>}
        {!setting.loading && !setting.error && (
          <Table<Schedule>
            rows={visible}
            columns={columns}
            rowKey={s => s.id}
            empty={
              <EmptyState
                icon={q ? Clock : CalendarClock}
                title={q ? 'Không có lịch nào khớp' : 'Chưa có lịch'}
                description={q ? 'Thử từ khoá khác.' : 'Tạo lịch đầu tiên để lưu cấu hình cron cho workflow.'}
                action={!q ? <Button variant="primary" icon={Plus} onClick={() => setEditing({ ...EMPTY_FORM })}>Lịch mới</Button> : undefined}
              />
            }
          />
        )}
      </div>
    </div>
  );
};

export default SchedulingCenter;
