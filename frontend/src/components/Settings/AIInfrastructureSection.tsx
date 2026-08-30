/**
 * Settings → AI Providers & Models.
 *
 *   providers tab   useLlmProviders()  GET /api/llm-providers + /health + /models
 *   failover        usePlatformSetting('failover_policies')  GET/PUT /api/platform/settings/failover_policies
 *   model registry  /api/llm-providers/models
 *   embeddings      models with mode == "embedding" + GET /api/knowledge/qdrant/collections
 */
import React, { useMemo, useState } from 'react';
import { Cpu, Database, Globe, Plus, RefreshCw, Search, Trash2, Pencil, Check, X, Layers } from 'lucide-react';
import { cn } from '../../lib/utils';
import {
  Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState, SectionTitle, Select, StatTile, Table, toneFor,
} from '../ui';
import { useLlmProviders, type LlmModel } from '../../lib/useLlmProviders';
import { usePlatformSetting, newSettingId } from '../../lib/usePlatformSettings';
import { usePlatformVectorCollections } from '../../lib/usePlatformData';

type Tab = 'PROVIDERS' | 'MODEL_REGISTRY' | 'EMBEDDINGS';

export interface FailoverPolicy {
  id: string;
  name: string;
  trigger: string;
  from_model: string;
  to_model: string;
  enabled: boolean;
}

const EMPTY_POLICY: Omit<FailoverPolicy, 'id'> = { name: '', trigger: '', from_model: '', to_model: '', enabled: true };

const costPerM = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `$${(v * 1_000_000).toFixed(2)} / 1M`;

const fmtTokens = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : v.toLocaleString('en-US');

export const AIInfrastructureSection = () => {
  const [tab, setTab] = useState<Tab>('PROVIDERS');
  const llm = useLlmProviders();

  return (
    <div className="space-y-6">
      <SectionTitle
        title="AI Providers & Models"
        subtitle="Nhà cung cấp LLM, registry model và hạ tầng embedding — đọc trực tiếp từ workflow_runtime."
        actions={<Button icon={RefreshCw} loading={llm.loading} onClick={llm.reload}>Tải lại</Button>}
      />

      <div className="flex bg-[#FFF9E8] p-1 rounded-xl border border-[#BFA66A] w-fit">
        {([
          { id: 'PROVIDERS', label: 'Providers', icon: Globe },
          { id: 'MODEL_REGISTRY', label: 'Model registry', icon: Cpu },
          { id: 'EMBEDDINGS', label: 'Embeddings', icon: Database },
        ] as { id: Tab; label: string; icon: typeof Globe }[]).map(item => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all',
              tab === item.id ? 'bg-white text-[#111111] border border-[#BFA66A] shadow-sm' : 'text-[#3F3F3F] hover:text-[#111111]',
            )}
          >
            <item.icon className={cn('w-3.5 h-3.5', tab === item.id ? 'text-[#8A5A00]' : 'text-[#5F5F5F]')} />
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'PROVIDERS' && <ProvidersTab llm={llm} />}
      {tab === 'MODEL_REGISTRY' && <ModelRegistryTab llm={llm} />}
      {tab === 'EMBEDDINGS' && <EmbeddingsTab llm={llm} />}
    </div>
  );
};

/* ─── Providers ──────────────────────────────────────────── */
const ProvidersTab = ({ llm }: { llm: ReturnType<typeof useLlmProviders> }) => {
  return (
    <div className="space-y-6">
      {llm.errors.providers ? <ErrorState message={llm.errors.providers} onRetry={llm.reload} /> : llm.loading ? <LoadingState /> : llm.providers.length === 0 ? (
        <EmptyState icon={Globe} title="Chưa có provider" description="workflow_runtime chưa đăng ký LLM provider nào (GET /api/llm-providers)." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {llm.providers.map(p => {
            const h = llm.healthFor(p.model_id) ?? llm.healthFor(p.name);
            const m = llm.modelFor(p.model_id) ?? llm.modelFor(p.name);
            return (
              <Card key={p.id} title={p.name} subtitle={<span className="font-mono">{p.model_id}</span>} actions={
                <div className="flex items-center gap-1.5">
                  {p.is_default && <Badge tone="dark">default</Badge>}
                  <Badge tone={h ? toneFor(h.status) : 'draft'}>{h ? h.status : 'no health'}</Badge>
                </div>
              }>
                <dl className="grid grid-cols-2 gap-3 text-xs">
                  <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                    <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Latency</dt>
                    <dd className="font-mono font-bold text-[#111111] mt-0.5">{h?.latency_ms != null ? `${Math.round(h.latency_ms)} ms` : '—'}</dd>
                  </div>
                  <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                    <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Type</dt>
                    <dd className="font-mono font-bold text-[#111111] mt-0.5">{p.type}</dd>
                  </div>
                  <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                    <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Input cost</dt>
                    <dd className="font-mono font-bold text-[#111111] mt-0.5">{costPerM(m?.input_cost_per_token)}</dd>
                  </div>
                  <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                    <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Output cost</dt>
                    <dd className="font-mono font-bold text-[#111111] mt-0.5">{costPerM(m?.output_cost_per_token)}</dd>
                  </div>
                </dl>
                {h?.error && <p className="mt-3 text-[11px] font-mono text-[#7A1010] break-all">{h.error}</p>}
              </Card>
            );
          })}
        </div>
      )}

      <FailoverPolicies modelNames={[...new Set([...llm.providers.map(p => p.model_id), ...llm.models.map(m => m.model_name)])]} />
    </div>
  );
};

/* ─── Failover policies (persisted) ──────────────────────── */
const FailoverPolicies = ({ modelNames }: { modelNames: string[] }) => {
  const setting = usePlatformSetting<FailoverPolicy[]>('failover_policies', []);
  const [editing, setEditing] = useState<FailoverPolicy | null>(null);
  const [draft, setDraft] = useState<Omit<FailoverPolicy, 'id'>>(EMPTY_POLICY);
  const [showForm, setShowForm] = useState(false);

  const policies = Array.isArray(setting.value) ? setting.value : [];

  const startAdd = () => { setEditing(null); setDraft(EMPTY_POLICY); setShowForm(true); };
  const startEdit = (p: FailoverPolicy) => { setEditing(p); setDraft({ name: p.name, trigger: p.trigger, from_model: p.from_model, to_model: p.to_model, enabled: p.enabled }); setShowForm(true); };
  const cancel = () => { setShowForm(false); setEditing(null); };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.name.trim() || !draft.trigger.trim()) return;
    const next = editing
      ? policies.map(p => (p.id === editing.id ? { ...editing, ...draft } : p))
      : [...policies, { id: newSettingId('fo'), ...draft }];
    await setting.save(next).then(cancel).catch(() => {});
  };
  const toggle = (p: FailoverPolicy) => setting.save(policies.map(x => (x.id === p.id ? { ...x, enabled: !x.enabled } : x))).catch(() => {});
  const remove = (p: FailoverPolicy) => {
    if (!window.confirm(`Xoá policy "${p.name}"?`)) return;
    setting.save(policies.filter(x => x.id !== p.id)).catch(() => {});
  };

  return (
    <Card
      title="Failover policies"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/failover_policies</code>{setting.updatedAt ? ` · cập nhật ${new Date(setting.updatedAt).toLocaleString('vi-VN')}` : ''}</>}
      actions={<Button size="sm" variant="primary" icon={Plus} onClick={startAdd} disabled={setting.loading}>Thêm policy</Button>}
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}

      {showForm && (
        <form onSubmit={submit} className="mb-5 p-4 rounded-xl border border-[#BFA66A] bg-[#FFF9E8] grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Tên"><Input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="vd. latency-fallback" required /></Field>
          <Field label="Trigger" hint="Điều kiện kích hoạt, vd. latency_ms > 2000 hoặc error_rate > 0.01">
            <Input value={draft.trigger} onChange={e => setDraft({ ...draft, trigger: e.target.value })} placeholder="latency_ms > 2000" required />
          </Field>
          <Field label="Từ model">
            <Input list="failover-models" value={draft.from_model} onChange={e => setDraft({ ...draft, from_model: e.target.value })} placeholder="model_id" />
          </Field>
          <Field label="Sang model">
            <Input list="failover-models" value={draft.to_model} onChange={e => setDraft({ ...draft, to_model: e.target.value })} placeholder="model_id" />
          </Field>
          <datalist id="failover-models">{modelNames.map(n => <option key={n} value={n} />)}</datalist>
          <label className="flex items-center gap-2 text-xs font-semibold text-[#3F3F3F]">
            <input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Bật ngay
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" icon={X} onClick={cancel}>Huỷ</Button>
            <Button type="submit" variant="primary" size="sm" icon={Check} loading={setting.saving}>{editing ? 'Lưu' : 'Thêm'}</Button>
          </div>
        </form>
      )}

      {setting.loading ? <LoadingState /> : policies.length === 0 ? (
        <EmptyState icon={Layers} title="Chưa có failover policy" description="Thêm policy để định nghĩa model dự phòng khi provider chính lỗi hoặc chậm." />
      ) : (
        <Table<FailoverPolicy>
          rows={policies}
          rowKey={p => p.id}
          columns={[
            { header: 'Policy', cell: p => <span className="font-bold">{p.name}</span> },
            { header: 'Trigger', cell: p => <code className="font-mono text-[11px]">{p.trigger}</code> },
            { header: 'Route', cell: p => <span className="font-mono text-[11px]">{p.from_model || '—'} → {p.to_model || '—'}</span> },
            { header: 'Trạng thái', cell: p => <Badge tone={p.enabled ? 'healthy' : 'draft'}>{p.enabled ? 'enabled' : 'disabled'}</Badge> },
            {
              header: '', align: 'right', cell: p => (
                <div className="flex justify-end gap-1">
                  <Button size="sm" variant="ghost" onClick={() => toggle(p)} disabled={setting.saving}>{p.enabled ? 'Tắt' : 'Bật'}</Button>
                  <Button size="sm" variant="ghost" icon={Pencil} onClick={() => startEdit(p)} aria-label="Sửa" />
                  <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(p)} aria-label="Xoá" />
                </div>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
};

/* ─── Model registry ─────────────────────────────────────── */
const ModelRegistryTab = ({ llm }: { llm: ReturnType<typeof useLlmProviders> }) => {
  const [q, setQ] = useState('');
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? llm.models.filter(m => `${m.model_name} ${m.litellm_provider} ${m.base_model ?? ''} ${m.mode}`.toLowerCase().includes(s)) : llm.models;
  }, [llm.models, q]);

  return (
    <Card
      title="Model registry"
      subtitle={<>GET /api/llm-providers/models · {llm.modelsAvailable ? `${llm.models.length} model` : 'nguồn không khả dụng'}</>}
      actions={
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#5F5F5F]" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Tìm model…" className="pl-8 w-56" />
        </div>
      }
      padding="none"
    >
      {llm.errors.models ? <div className="p-4"><ErrorState message={llm.errors.models} onRetry={llm.reload} /></div> : llm.loading ? <LoadingState /> : (
        <Table<LlmModel>
          rows={rows}
          rowKey={m => m.model_name}
          empty={<EmptyState icon={Cpu} title={llm.models.length === 0 ? 'Registry trống' : 'Không có model khớp'} description={llm.models.length === 0 ? 'workflow_runtime chưa expose model nào (LiteLLM model list không khả dụng).' : undefined} />}
          columns={[
            { header: 'Model', cell: m => <span className="font-mono font-bold">{m.model_name}</span> },
            { header: 'Provider', cell: m => m.litellm_provider },
            { header: 'Mode', cell: m => <Badge tone={m.mode === 'embedding' ? 'running' : 'draft'}>{m.mode}</Badge> },
            { header: 'Max tokens', align: 'right', cell: m => <span className="font-mono">{fmtTokens(m.max_tokens)}</span> },
            { header: 'Input', align: 'right', cell: m => <span className="font-mono">{costPerM(m.input_cost_per_token)}</span> },
            { header: 'Output', align: 'right', cell: m => <span className="font-mono">{costPerM(m.output_cost_per_token)}</span> },
          ]}
        />
      )}
    </Card>
  );
};

/* ─── Embeddings ─────────────────────────────────────────── */
const EmbeddingsTab = ({ llm }: { llm: ReturnType<typeof useLlmProviders> }) => {
  const embedders = llm.models.filter(m => m.mode === 'embedding');
  const collections = usePlatformVectorCollections();
  const rows = collections.data ?? [];
  const totalPoints = rows.reduce((a, c) => a + (c.total_points ?? 0), 0);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Embedding models" value={embedders.length} icon={Cpu} loading={llm.loading} />
        <StatTile label="Vector collections" value={rows.length} icon={Database} loading={collections.loading} />
        <StatTile label="Tổng points" value={totalPoints.toLocaleString('en-US')} icon={Layers} loading={collections.loading} />
        <StatTile label="Collections active" value={rows.filter(c => c.active).length} icon={Database} tone="ok" loading={collections.loading} />
      </div>

      <Card title="Embedding models" subtitle="mode == embedding từ /api/llm-providers/models" padding="none">
        {llm.errors.models ? <div className="p-4"><ErrorState message={llm.errors.models} onRetry={llm.reload} /></div> : llm.loading ? <LoadingState /> : (
          <Table<LlmModel>
            rows={embedders}
            rowKey={m => m.model_name}
            empty={<EmptyState icon={Cpu} title="Không có embedding model" description="Registry không có model nào với mode = embedding." />}
            columns={[
              { header: 'Model', cell: m => <span className="font-mono font-bold">{m.model_name}</span> },
              { header: 'Provider', cell: m => m.litellm_provider },
              { header: 'Max tokens', align: 'right', cell: m => <span className="font-mono">{fmtTokens(m.max_tokens)}</span> },
              { header: 'Input cost', align: 'right', cell: m => <span className="font-mono">{costPerM(m.input_cost_per_token)}</span> },
            ]}
          />
        )}
      </Card>

      <Card
        title="Vector collections (Qdrant)"
        subtitle="GET /api/knowledge/qdrant/collections"
        actions={<Button size="sm" icon={RefreshCw} loading={collections.loading} onClick={collections.reload}>Tải lại</Button>}
        padding="none"
      >
        {collections.error ? <div className="p-4"><ErrorState message={collections.error} onRetry={collections.reload} /></div> : collections.loading ? <LoadingState /> : (
          <Table
            rows={rows}
            rowKey={c => c.id}
            empty={<EmptyState icon={Database} title="Chưa có collection" description="Qdrant chưa có collection nào cho tenant này." />}
            columns={[
              { header: 'Collection', cell: c => <span className="font-mono font-bold">{c.name}</span> },
              { header: 'Embedding', cell: c => c.embedding_model || '—' },
              { header: 'Dim / distance', cell: c => <span className="font-mono">{c.dimensions} · {c.distance}</span> },
              { header: 'Points (tenant / total)', align: 'right', cell: c => <span className="font-mono">{c.points.toLocaleString('en-US')} / {c.total_points.toLocaleString('en-US')}</span> },
              { header: 'Indexed', align: 'right', cell: c => <span className="font-mono">{c.indexed}%</span> },
              { header: 'Trạng thái', cell: c => <Badge tone={c.active ? 'healthy' : 'draft'}>{c.active ? 'active' : 'inactive'}</Badge> },
            ]}
          />
        )}
      </Card>
    </div>
  );
};
