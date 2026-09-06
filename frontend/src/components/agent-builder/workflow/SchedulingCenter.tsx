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
  weekly_report: 'Weekly narrative',
  data_quality: 'Data quality',
  anomaly: 'Anomaly',
  forecast: 'Forecast',
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
      <Field label="Schedule name">
        <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Daily KB sync" autoFocus />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Agent" hint={agents.error ? 'Could not load the agent list' : undefined}>
          <Select value={form.agent_id} onChange={e => setForm(f => ({ ...f, agent_id: e.target.value, workflow_id: '' }))}>
            <option value="">— Select agent —</option>
            {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        </Field>
        <Field label="Workflow" hint={form.agent_id && !workflows.loading && wfList.length === 0 ? 'This agent has no workflow yet' : undefined}>
          <Select value={form.workflow_id} onChange={e => setForm(f => ({ ...f, workflow_id: e.target.value }))} disabled={!form.agent_id || workflows.loading}>
            <option value="">{workflows.loading ? 'Loading…' : '— Select workflow —'}</option>
            {wfList.map(w => <option key={w.id} value={w.id}>{w.name}{w.published_version_id ? '' : ' (not published)'}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Cron" hint="5 fields: minute hour day month weekday (server time). e.g. 0 */4 * * * = every 4 hours.">
        <Input value={form.cron} onChange={e => setForm(f => ({ ...f, cron: e.target.value }))} className="font-mono" placeholder="0 9 * * 1-5" />
      </Field>
      <Field label="Job type">
        <Select value={form.report_kind} onChange={e => setForm(f => ({ ...f, report_kind: e.target.value as FormState['report_kind'] }))}>
          {Object.entries(REPORT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </Select>
      </Field>
      <label className="flex items-center gap-2 text-[12px] text-[#111111]">
        <input type="checkbox" checked={form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} className="accent-[#B88719]" />
        Enable this schedule
      </label>
      <div className="flex justify-end gap-2 pt-2 border-t border-[#BFA66A]">
        <Button variant="ghost" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button type="submit" variant="primary" loading={saving} disabled={!valid}>{form.id ? 'Save changes' : 'Create schedule'}</Button>
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
      setActionError(e instanceof Error ? e.message : 'Failed to save');
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
    if (!window.confirm(`Delete schedule "${s.name}"?`)) return;
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
      setActionError(e instanceof Error ? e.message : 'Failed to save the delivery channel');
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
      setActionError(e instanceof Error ? e.message : 'Failed to run the use case');
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
      setActionError(e instanceof Error ? e.message : 'Failed to run the scheduler');
    } finally {
      setRunning(null);
    }
  };

  const q = query.trim().toLowerCase();
  const visible = schedules.filter(s => !q || `${s.name} ${s.cron} ${s.workflow_name ?? ''}`.toLowerCase().includes(q));
  const enabledCount = schedules.filter(s => s.enabled).length;

  const columns: Column<Schedule>[] = [
    {
      header: 'Schedule',
      cell: s => (
        <div>
          <div className="font-semibold text-[#111111]">{s.name}</div>
          <div className="text-[11px] text-[#5F5F5F]">{s.workflow_name ?? (s.workflow_id ? s.workflow_id : 'No workflow attached')}</div>
        </div>
      ),
    },
    { header: 'Cron', cell: s => <code className="font-mono text-[12px] text-[#111111]">{s.cron}</code> },
    { header: 'Job', cell: s => <span className="text-[#3F3F3F]">{REPORT_LABELS[s.report_kind ?? 'weekly_report']}</span> },
    { header: 'Status', cell: s => <Badge tone={s.enabled ? 'healthy' : 'draft'}>{s.enabled ? 'On' : 'Off'}</Badge> },
    { header: 'Last run', cell: s => <span className="text-[#3F3F3F]">{s.last_run ? formatDateTime(s.last_run) : '—'}</span> },
    {
      header: '',
      align: 'right',
      cell: s => (
        <div className="flex items-center justify-end gap-1">
          <Button size="sm" variant="ghost" icon={Power} onClick={() => void toggle(s)} disabled={setting.saving} aria-label={s.enabled ? 'Turn off' : 'Turn on'}>
            {s.enabled ? 'Off' : 'On'}
          </Button>
          <Button size="sm" variant="ghost" icon={Pencil} onClick={() => edit(s)} disabled={setting.saving}>Edit</Button>
          <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(s)} disabled={setting.saving} aria-label="Delete schedule" />
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Scheduling"
        subtitle="Cron schedules for workflow runs, periodic reports, DQ, anomaly, and forecast jobs."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="ghost" icon={RefreshCw} onClick={() => void roi.refetch()} loading={roi.loading}>Refresh</Button>
            <Button variant="ghost" icon={Play} onClick={() => void runSchedulesNow()} loading={running === 'schedules'}>Run scheduler</Button>
            <Button variant="primary" icon={Plus} onClick={() => setEditing({ ...EMPTY_FORM })}>New schedule</Button>
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
                <Play size={10} /> Run now
              </button>
            )}
          </Card>
        ))}
        {!roi.loading && !roi.data && (
          <Card className="xl:col-span-5">
            <ErrorState message={roi.error ?? 'Could not load ROI use cases'} onRetry={() => void roi.refetch()} />
          </Card>
        )}
      </div>

      <Card title="Delivery channels" subtitle="Slack webhooks and email targets are stored tenant-scoped. Slack sends immediately if a webhook is set; email goes into the outbox.">
        <div className="grid grid-cols-1 lg:grid-cols-[160px_1fr_1fr_auto] gap-2">
          <Select value={channelKind} onChange={e => setChannelKind(e.target.value as DeliveryChannel['kind'])}>
            <option value="slack">Slack</option>
            <option value="email">Email</option>
          </Select>
          <Input value={channelTarget} onChange={e => setChannelTarget(e.target.value)} placeholder={channelKind === 'slack' ? '#data-alerts' : 'data-team@example.com'} />
          <Input value={channelWebhook} onChange={e => setChannelWebhook(e.target.value)} placeholder="Slack webhook URL (optional)" disabled={channelKind !== 'slack'} />
          <Button variant="primary" icon={Plus} onClick={() => void addChannel()} loading={delivery.saving}>Add channel</Button>
        </div>
        <div className="mt-3 divide-y divide-[#ECE7DA] border border-[#ECE7DA] rounded-xl overflow-hidden">
          {channels.length === 0 && <div className="p-3 text-[12px] text-[#5F5F5F]">No delivery channels yet.</div>}
          {channels.map(c => (
            <div key={c.id} className="flex items-center gap-3 p-3 bg-white">
              {c.kind === 'slack' ? <MessageSquare className="w-4 h-4 text-[#4A5FD8]" /> : <Mail className="w-4 h-4 text-[#1D6F4C]" />}
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-semibold text-[#111111] truncate">{c.target}</div>
                <div className="text-[10px] uppercase tracking-wide text-[#5F5F5F]">{c.kind}</div>
              </div>
              <Badge tone={c.enabled ? 'healthy' : 'draft'}>{c.enabled ? 'On' : 'Off'}</Badge>
              <Button size="sm" variant="ghost" icon={Power} onClick={() => toggleChannel(c.id)}>{c.enabled ? 'Off' : 'On'}</Button>
              <Button size="sm" variant="danger" icon={Trash2} onClick={() => removeChannel(c.id)} aria-label="Delete channel" />
            </div>
          ))}
        </div>
      </Card>

      {editing && (
        <Card title={editing.id ? 'Edit schedule' : 'New schedule'} subtitle="5-field cron, server time">
          <ScheduleForm initial={editing} saving={setting.saving} onCancel={() => setEditing(null)} onSubmit={handleSubmit} />
        </Card>
      )}

      {actionError && <ErrorState message={actionError} />}

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
          <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search schedule, cron, workflow…" className="pl-9 w-72" aria-label="Search schedules" />
        </div>
        <span className={cn('text-xs ml-auto', 'text-[#5F5F5F]')}>
          {schedules.length} schedules · {enabledCount} enabled{setting.updatedAt ? ` · updated ${formatDateTime(setting.updatedAt)}` : ''}
        </span>
      </div>

      <div className="content-card content-card--static overflow-hidden">
        {setting.loading && schedules.length === 0 && <LoadingState label="Loading schedules…" />}
        {setting.error && <div className="p-4"><ErrorState message={setting.error} onRetry={() => void setting.refetch()} /></div>}
        {!setting.loading && !setting.error && (
          <Table<Schedule>
            rows={visible}
            columns={columns}
            rowKey={s => s.id}
            empty={
              <EmptyState
                icon={q ? Clock : CalendarClock}
                title={q ? 'No matching schedules' : 'No schedules yet'}
                description={q ? 'Try a different search term.' : 'Create your first schedule to save a cron configuration for a workflow.'}
                action={!q ? <Button variant="primary" icon={Plus} onClick={() => setEditing({ ...EMPTY_FORM })}>New schedule</Button> : undefined}
              />
            }
          />
        )}
      </div>
    </div>
  );
};

export default SchedulingCenter;
