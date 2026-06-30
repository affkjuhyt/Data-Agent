import React, { useState, useRef, useEffect } from 'react';
import {
  MessageSquare, Send, Bug, ChevronDown,
  Clock, Zap, Bot, User, Layers, Search,
  Brain, FileOutput, AlertCircle,
  Route, Sparkles, Copy, GripHorizontal,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';
import { API_URL } from '../../lib/config';
import { apiGet, apiMutate } from '../../lib/apiClient';
import keycloak from '../../lib/keycloak';

/* ─── Types ─────────────────────────────────────────────────── */

interface TraceStep {
  id: string;
  node: string;
  status: 'success' | 'error' | 'running';
  latencyMs: number;
  startMs: number;
  tokens: number;
  cost: number;
  input: string;
  output: string;
  iteration: number;
  isRetryStart: boolean;
  verdict?: 'PASS' | 'FAIL';
  errorType?: string;
  agentConvId?: string;     // nếu có → task có thể expand để xem LangGraph trace bên trong
  agentLfTraceId?: string;  // orchestrator trace_id (orchestrated mode)
  agentLfSpanId?: string;   // span id của task trong orchestrator trace
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  suggestions?: string[];
  routed?: { name: string; conf: number } | null;
}

interface PendingApproval {
  convId: string;
  placeholderId: string;
  payload: { title: string; description: string; detail?: unknown } | null;
}

interface AgentOption {
  id: string;
  label: string;
  description: string;
  routing_keywords?: string[];
}

/* ─── Span palette ───────────────────────────────────────────── */

function spanType(node: string): { label: string; color: string; bg: string } {
  if (node.startsWith('[target]'))
    return { label: 'TARGET', color: '#C084FC', bg: 'rgba(192,132,252,0.14)' };
  if (node.startsWith('[task]'))
    return { label: 'TASK',   color: '#67E8F9', bg: 'rgba(103,232,249,0.12)' };
  if (node === 'router')
    return { label: 'ROUTING',    color: '#F0B429', bg: 'rgba(240,180,41,0.18)' };
  if (node.includes('guardrail'))
    return { label: 'GUARD',      color: '#F8A4A4', bg: 'rgba(248,164,164,0.14)' };
  if (node === 'orchestrator-synthesizer')
    return { label: 'SYNTH',      color: '#86EFAC', bg: 'rgba(134,239,172,0.12)' };
  if (node.includes('planner') || node.includes('rewrite') || node.includes('responder') || node.includes('suggestion') || node.includes('input_refine'))
    return { label: 'GENERATION', color: '#EBD07C', bg: 'rgba(235,208,124,0.14)' };
  if (node.includes('kb_search'))
    return { label: 'RETRIEVAL',  color: '#93C5FD', bg: 'rgba(147,197,253,0.12)' };
  if (node.includes('mcp') || node.includes('tool_executor') || node.startsWith('tool/'))
    return { label: 'TOOL',       color: '#86EFAC', bg: 'rgba(134,239,172,0.12)' };
  if (node.includes('rrf') || node.includes('reranker'))
    return { label: 'RANK',       color: '#F3E2A7', bg: 'rgba(243,226,167,0.10)' };
  if (node.includes('judge'))
    return { label: 'EVAL',       color: '#C084FC', bg: 'rgba(192,132,252,0.14)' };
  return   { label: 'SPAN',       color: '#BFA66A', bg: 'rgba(191,166,106,0.12)' };
}

/* ─── Helpers ────────────────────────────────────────────────── */

const _SKIP_NODES = new Set(['LangGraph', 'join_retrieval', 'join_end']);
const _shouldSkip = (name: string) => _SKIP_NODES.has(name) || name.startsWith('_route') || name.startsWith('route_');

function trimInput(name: string, raw: any): string {
  if (!raw || typeof raw !== 'object') return String(raw ?? '');
  const q   = raw.query ?? '';
  const cfg = raw.config ?? {};
  const clip = (s: string, n = 300) => s.length > n ? s.slice(0, n) + '…' : s;
  switch (true) {
    // ── Orchestrator spans ──
    case name.startsWith('[task]'):
      return JSON.stringify({ agent_name: raw.agent_name || raw.agent_id, agent_id: raw.agent_id, prompt: clip(raw.prompt ?? '') }, null, 2);
    case name.startsWith('[target]'):
      return JSON.stringify({ description: raw.description }, null, 2);
    case name === 'orchestrator-synthesizer':
      return JSON.stringify({ query: q, outputs_count: raw.outputs_count ?? 0 }, null, 2);
    case name === 'orchestrator-planner':
      return JSON.stringify({ query: q }, null, 2);
    // ── Agent (LangGraph) spans ──
    case name.includes('guardrail'):
      return JSON.stringify({ query: q }, null, 2);
    case name.includes('planner'):
      return JSON.stringify({ query: q, memory_context: (raw.memory_context ?? []).length + ' items' }, null, 2);
    case name.includes('input_refine'):
    case name.includes('rewrite'):
      return JSON.stringify({ query: q, messages: (raw.messages ?? []).length + ' messages' }, null, 2);
    case name.includes('kb_search'):
      return JSON.stringify({ query: q, mode: cfg.kb_mode, top_k: cfg.top_k }, null, 2);
    case name.includes('mcp'):
      return JSON.stringify({ query: q, tool_calls: (raw.tool_calls ?? []).length + ' calls' }, null, 2);
    case name.includes('rrf'):
      return JSON.stringify({ kb_chunks: (raw.kb_chunks ?? []).length + ' chunks', mcp_results: (raw.mcp_results ?? []).length + ' results' }, null, 2);
    case name.includes('reranker'):
      return JSON.stringify({ rrf_results: (raw.rrf_results ?? []).length + ' items', top_n: cfg.reranker_top_n }, null, 2);
    case name.includes('responder'):
      return JSON.stringify({ query: q, reranked_chunks: (raw.reranked_chunks ?? []).length + ' chunks' }, null, 2);
    case name.includes('suggestion'):
      return JSON.stringify({ query: q, response: clip(raw.response ?? '') }, null, 2);
    case name.includes('judge'):
      return JSON.stringify({ query: q, response: clip(raw.response ?? ''), tool_errors: (raw.tool_errors ?? []).length + ' errors' }, null, 2);
    default:
      return JSON.stringify(raw, null, 2);
  }
}

function mapObservations(obs: any[], traceId?: string): TraceStep[] {
  const byTime = obs
    .filter(o => !_shouldSkip(o.name) && (o.type === 'SPAN' || o.name?.startsWith('judge')))
    .sort((a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime());

  // Re-order: [target] spans must always appear before their child [task] spans.
  // Async timing in the backend can cause target_span.startTime to land slightly
  // after the first task's startTime in Langfuse, producing wrong sort order.
  const taskPool  = byTime.filter(o => o.name.startsWith('[task]'));
  const nonTasks  = byTime.filter(o => !o.name.startsWith('[task]'));
  const placed    = new Set<string>();
  const ordered: typeof byTime = [];
  for (const span of nonTasks) {
    ordered.push(span);
    if (span.name.startsWith('[target]')) {
      const tStart = new Date(span.startTime).getTime();
      const tEnd   = span.endTime ? new Date(span.endTime).getTime() : Infinity;
      const children = taskPool
        .filter(t => !placed.has(t.id) && new Date(t.startTime).getTime() >= tStart && new Date(t.startTime).getTime() <= tEnd)
        .sort((a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime());
      for (const c of children) { placed.add(c.id); ordered.push(c); }
    }
  }
  for (const t of taskPool) { if (!placed.has(t.id)) ordered.push(t); }

  const filtered = ordered;
  const traceStart = filtered.length > 0 ? new Date(filtered[0].startTime).getTime() : 0;
  const nameCounts: Record<string, number> = {};
  let currentMaxIteration = 0;
  return filtered.map(o => {
    const count = nameCounts[o.name] ?? 0;
    nameCounts[o.name] = count + 1;
    const iteration = count;
    const isRetryStart = iteration > currentMaxIteration;
    if (isRetryStart) currentMaxIteration = iteration;
    const displayName = iteration > 0 ? `${o.name}#${iteration}` : o.name;
    let verdict: 'PASS' | 'FAIL' | undefined;
    let errorType: string | undefined;
    if (o.name.startsWith('judge')) {
      try {
        const out = typeof o.output === 'object' ? o.output : JSON.parse(o.output || '{}');
        verdict   = out.judge_verdict;
        errorType = out.judge_error_type ?? undefined;
      } catch { /* ignore */ }
    }
    // [task] và single-agent spans có thể expand để xem LangGraph trace bên trong
    const isExpandable = o.name?.startsWith('[task]') || o.name === 'single-agent';
    const agentConvId = isExpandable
      ? (typeof o.metadata === 'object' && o.metadata !== null ? (o.metadata as any).conversation_id ?? undefined : undefined)
      : undefined;
    // Dùng o.id (= span id trong Langfuse) để proxy fetch đúng observations khi orchestrated
    const agentLfTraceId = isExpandable ? traceId : undefined;
    const agentLfSpanId  = isExpandable ? o.id : undefined;
    return {
      id: o.id,
      node: displayName,
      status: o.level === 'ERROR' ? 'error' : 'success',
      startMs: Math.round(new Date(o.startTime).getTime() - traceStart),
      latencyMs: o.endTime ? Math.round(new Date(o.endTime).getTime() - new Date(o.startTime).getTime()) : 0,
      tokens: o.usage ? ((o.usage.input ?? 0) + (o.usage.output ?? 0)) : 0,
      cost: o.calculatedTotalCost ?? 0,
      input: trimInput(o.name, o.input),
      output: o.level === 'ERROR' && o.statusMessage
        ? o.statusMessage
        : typeof o.output === 'object' ? JSON.stringify(o.output, null, 2) : String(o.output ?? ''),
      iteration,
      isRetryStart,
      verdict,
      errorType,
      agentConvId,
      agentLfTraceId,
      agentLfSpanId,
    };
  });
}

/* ─── Routing ────────────────────────────────────────────────── */

function routeMessage(text: string, agents: AgentOption[]): { name: string; conf: number } {
  const q = text.toLowerCase();
  const scored = agents.map(a => {
    const kws = a.routing_keywords ?? [];
    const hits = kws.filter(k => q.includes(k.toLowerCase()));
    return { a, score: Math.min(1, hits.length / 2) };
  }).sort((x, y) => y.score - x.score);
  const top = scored[0];
  const conf = top && top.score > 0
    ? Math.min(0.98, 0.5 + top.score * 0.48)
    : 0.28 + Math.random() * 0.15;
  return { name: top?.a.label ?? 'Fallback', conf };
}

/* ─── WaterfallRow ───────────────────────────────────────────── */

function WaterfallRow({ step, index, selected, onSelect, startMs, totalMs, indent, expandable, expanded, expandLoading, onExpand }: {
  step: TraceStep; index: number; selected: boolean;
  onSelect: () => void; startMs: number; totalMs: number;
  indent?: boolean; expandable?: boolean; expanded?: boolean;
  expandLoading?: boolean; onExpand?: () => void;
}) {
  const t = spanType(step.node);
  const leftPct  = (startMs / Math.max(totalMs, 1)) * 100;
  const widthPct = Math.max(0.6, (step.latencyMs / Math.max(totalMs, 1)) * 100);
  const isErr = step.status === 'error';
  // strip prefixes for display; TASK/TARGET labels already shown in chip, title shown in detail panel
  const displayNode = step.node.replace(/^\[(target|task)\]\s*/, '');
  const isOrchNode = step.node.startsWith('[target]') || step.node.startsWith('[task]');
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', paddingLeft: indent ? 20 : 0, borderLeft: indent ? '2px solid rgba(103,232,249,0.2)' : 'none', marginLeft: indent ? 12 : 0 }}>
      <div style={{ flexShrink: 0, width: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {expandable && (
          <button
            onClick={e => { e.stopPropagation(); onExpand?.(); }}
            style={{ width: 18, height: '100%', background: 'transparent', border: 'none', cursor: 'pointer', color: 'rgba(103,232,249,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0 }}
            title="Xem trace agent"
          >
            {expandLoading
              ? <span style={{ fontSize: 9 }}>⟳</span>
              : <ChevronDown size={10} style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)', transition: 'transform 0.15s' }} />
            }
          </button>
        )}
      </div>
    <button
      onClick={() => onSelect()}
      style={{
        flex: 1, display: 'grid',
        gridTemplateColumns: '176px 1fr 52px',
        alignItems: 'center', gap: 8, padding: '5px 12px',
        background: selected ? 'rgba(235,208,124,0.10)' : 'transparent',
        border: 'none',
        borderLeft: selected ? '2px solid #EBD07C' : '2px solid transparent',
        textAlign: 'left', cursor: 'pointer',
      }}
      onMouseEnter={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.035)'; }}
      onMouseLeave={e => { if (!selected) (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <span style={{ fontSize: 8, fontWeight: 700, padding: '2px 5px', borderRadius: 3, background: t.bg, color: t.color, letterSpacing: '0.04em', flexShrink: 0 }}>
          {t.label}
        </span>
        {!isOrchNode && (
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.88)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {displayNode}
          </span>
        )}
      </div>
      {step.verdict ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            fontSize: 9, fontWeight: 700, padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em',
            background: step.verdict === 'PASS' ? 'rgba(52,211,153,0.18)' : 'rgba(248,113,113,0.18)',
            color: step.verdict === 'PASS' ? '#34D399' : '#F87171',
          }}>
            {step.verdict}
          </span>
          {step.errorType && (
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.38)', fontFamily: 'var(--font-mono)' }}>
              {step.errorType}
            </span>
          )}
        </div>
      ) : (
        <div style={{ position: 'relative', height: 14 }}>
          <div style={{ position: 'absolute', top: 5, left: 0, right: 0, height: 4, background: 'rgba(255,255,255,0.04)', borderRadius: 2 }} />
          <div style={{
            position: 'absolute', top: 4,
            left: leftPct + '%', width: widthPct + '%',
            height: 6, borderRadius: 2,
            background: isErr ? '#F8A4A4' : t.color,
            boxShadow: '0 0 0 1px rgba(255,255,255,0.08)',
          }} />
        </div>
      )}
      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.45)', textAlign: 'right' }}>
        {step.latencyMs}ms
      </span>
    </button>
    </div>
  );
}

/* ─── JsonView ───────────────────────────────────────────────── */

function JsonView({ value }: { value: string | object }) {
  const txt = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return (
    <pre style={{
      background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 6, padding: '10px 12px', fontSize: 11,
      fontFamily: 'var(--font-mono)', color: 'rgba(229,231,235,0.78)',
      margin: 0, whiteSpace: 'pre-wrap', overflowX: 'auto', lineHeight: 1.55,
    }}>
      {txt}
    </pre>
  );
}

/* ─── TracePanel ─────────────────────────────────────────────── */

function TracePanel({ steps, traceLoading, traceId }: {
  steps: TraceStep[]; traceLoading: boolean; traceId: string | null;
}) {
  const [tab, setTab]           = useState<'input' | 'output' | 'metadata'>('input');
  // Track by ID instead of index to prevent index-shift when expanding rows
  const [selectedId, setSelectedId] = useState<string>('');
  const [splitPct, setSplitPct] = useState(40);
  const isDragging    = useRef(false);
  const containerRef  = useRef<HTMLDivElement>(null);

  // expand state cho task rows
  const [expanded,    setExpanded]    = useState<Set<string>>(new Set());
  const [subSteps,    setSubSteps]    = useState<Record<string, TraceStep[]>>({});
  const [loadingConv, setLoadingConv] = useState<Set<string>>(new Set());

  const toggleExpand = async (step: TraceStep) => {
    const convId = step.agentConvId!;
    const next = new Set(expanded);
    if (next.has(convId)) { next.delete(convId); setExpanded(next); return; }
    next.add(convId);
    setExpanded(next);
    if (subSteps[convId]) return; // already fetched
    setLoadingConv(prev => new Set(prev).add(convId));
    // Orchestrated mode: spans nằm trong orchestrator trace, không phải trace riêng của conv
    const url = (step.agentLfTraceId && step.agentLfSpanId)
      ? `/api/traces/${convId}?trace_id=${step.agentLfTraceId}&parent_observation_id=${step.agentLfSpanId}`
      : `/api/traces/${convId}`;
    for (let i = 0; i < 5; i++) {
      try {
        const { observations } = await apiGet<any>(url);
        const mapped = mapObservations(observations ?? []);
        if (mapped.length > 0) { setSubSteps(prev => ({ ...prev, [convId]: mapped })); break; }
      } catch { /* ignore */ }
      if (i < 4) await new Promise(r => setTimeout(r, 1500));
    }
    setLoadingConv(prev => { const s = new Set(prev); s.delete(convId); return s; });
  };

  // Reset to first row when steps change (new result)
  useEffect(() => {
    if (steps.length > 0) { setSelectedId(steps[0].id); setTab('input'); }
  }, [steps]);

  /* Drag handlers */
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const pct  = ((e.clientY - rect.top) / rect.height) * 100;
      setSplitPct(Math.min(75, Math.max(20, pct)));
    };
    const onUp = () => { isDragging.current = false; document.body.style.cursor = ''; };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, []);

  // flat list: steps + sub-steps interleaved (task rows expand inline)
  const allRows: Array<{ step: TraceStep; indent: boolean }> = [];
  for (const step of steps) {
    allRows.push({ step, indent: false });
    if (step.agentConvId && expanded.has(step.agentConvId)) {
      for (const sub of (subSteps[step.agentConvId] ?? [])) {
        allRows.push({ step: { ...sub, startMs: sub.startMs + step.startMs }, indent: true });
      }
      if (loadingConv.has(step.agentConvId) && !subSteps[step.agentConvId]) {
        allRows.push({ step: { ...step, id: step.id + '-loading', node: '…loading', status: 'running', latencyMs: 0, tokens: 0, cost: 0, input: '', output: '', iteration: 0, isRetryStart: false }, indent: true });
      }
    }
  }
  const headerTotalMs = Math.max(...steps.map(s => s.startMs + s.latencyMs), 1);
  const totalMs  = Math.max(...allRows.map(r => r.step.startMs + r.step.latencyMs), 1);
  const totalTok = allRows.reduce((s, r) => s + r.step.tokens, 0);
  // Find selected by ID — immune to index shifts from expand/collapse
  const sel = allRows.find(r => r.step.id === selectedId)?.step ?? allRows[0]?.step;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#0B0B0E', minWidth: 0 }}>

      {/* ── Trace header ── */}
      <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
          <span style={{ fontSize: 8, fontWeight: 700, padding: '2px 6px', borderRadius: 3, background: 'rgba(235,208,124,0.18)', color: '#EBD07C', letterSpacing: '0.06em' }}>
            TRACE
          </span>
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.8)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            chat.completion
          </span>
          {traceId && (
            <button
              onClick={() => navigator.clipboard.writeText(traceId)}
              style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'rgba(255,255,255,0.45)', borderRadius: 5, padding: '2px 6px', fontSize: 9.5, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3 }}
            >
              <Copy size={8} /> ID
            </button>
          )}
        </div>
        {traceId && (
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ color: 'rgba(255,255,255,0.25)' }}>id </span>{traceId.slice(0, 20)}…
          </div>
        )}
      </div>

      {/* ── KPI strip ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
        {[
          { k: 'latency', v: (headerTotalMs / 1000).toFixed(2) + 's', c: '#F5F5F5' },
          { k: 'tokens',  v: totalTok.toLocaleString(),          c: '#EBD07C' },
          { k: 'cost',    v: '$' + steps.reduce((s, t) => s + t.cost, 0).toFixed(4), c: '#C5DD83' },
          { k: 'spans',   v: String(steps.length),               c: '#F3E2A7' },
        ].map(({ k, v, c }, i) => (
          <div key={k} style={{ padding: '7px 10px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.04)' : 'none' }}>
            <div style={{ fontSize: 8.5, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'rgba(255,255,255,0.3)', marginBottom: 2 }}>{k}</div>
            <div style={{ fontSize: 12, fontWeight: 600, color: c, fontFamily: 'var(--font-mono)' }}>{v}</div>
          </div>
        ))}
      </div>

      {/* ── Resizable split container ── */}
      <div ref={containerRef} style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>

        {/* Waterfall section */}
        <div style={{ height: splitPct + '%', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          {/* label */}
          <div style={{ padding: '8px 12px 4px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
            <span style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'rgba(255,255,255,0.38)', fontWeight: 600 }}>
              Observations
            </span>
            <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.28)', fontFamily: 'var(--font-mono)' }}>
              {traceLoading ? 'streaming…' : steps.length + ' spans · ' + headerTotalMs + 'ms'}
            </span>
          </div>
          {/* rows */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '2px 0 4px' }}>
            {traceLoading ? (
              <div style={{ padding: '4px 12px', display: 'flex', flexDirection: 'column', gap: 5 }}>
                {[0, 1, 2, 3].map(i => (
                  <div key={i} className="pulse-soft" style={{ height: 22, borderRadius: 4, background: 'rgba(255,255,255,0.04)', opacity: 1 - i * 0.18 }} />
                ))}
              </div>
            ) : allRows.length === 0 ? (
              <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', textAlign: 'center', padding: '20px 12px' }}>
                Send a message to capture a trace
              </p>
            ) : allRows.map(({ step: s, indent }, i) => (
              <React.Fragment key={s.id + i}>
                {s.isRetryStart && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 12px 2px',
                    color: 'rgba(192,132,252,0.7)', fontSize: 9,
                    fontFamily: 'var(--font-mono)', letterSpacing: '0.08em',
                  }}>
                    <div style={{ flex: 1, height: 1, background: 'rgba(192,132,252,0.25)' }} />
                    <span>retry #{s.iteration}</span>
                    <div style={{ flex: 1, height: 1, background: 'rgba(192,132,252,0.25)' }} />
                  </div>
                )}
                <WaterfallRow
                  step={s} index={i}
                  selected={selectedId === s.id} onSelect={() => { setSelectedId(s.id); setTab('input'); }}
                  startMs={s.startMs} totalMs={totalMs}
                  indent={indent}
                  expandable={!!s.agentConvId}
                  expanded={!!s.agentConvId && expanded.has(s.agentConvId)}
                  expandLoading={!!s.agentConvId && loadingConv.has(s.agentConvId)}
                  onExpand={() => s.agentConvId && toggleExpand(s)}
                />
              </React.Fragment>
            ))}
          </div>
        </div>

        {/* ── Drag divider ── */}
        <div
          onMouseDown={() => { isDragging.current = true; document.body.style.cursor = 'row-resize'; }}
          style={{
            flexShrink: 0, height: 10, cursor: 'row-resize',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(255,255,255,0.02)',
            borderTop: '1px solid rgba(255,255,255,0.07)',
            borderBottom: '1px solid rgba(255,255,255,0.07)',
            userSelect: 'none',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(235,208,124,0.07)'; }}
          onMouseLeave={e => { if (!isDragging.current) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.02)'; }}
        >
          <GripHorizontal size={12} style={{ color: 'rgba(255,255,255,0.2)', pointerEvents: 'none' }} />
        </div>

        {/* Span Detail section */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          {!traceLoading && sel ? (
            <>
              {/* span header */}
              <div style={{ padding: '7px 14px 5px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 0 }}>
                {(() => { const t = spanType(sel.node); return <span style={{ fontSize: 8, fontWeight: 700, padding: '2px 5px', borderRadius: 3, background: t.bg, color: t.color }}>{t.label}</span>; })()}
                <span style={{ fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.88)', fontWeight: 500 }}>{sel.node.replace(/^\[(target|task)\]\s*/, '')}</span>
                <span style={{ marginLeft: 'auto', fontSize: 9.5, color: 'rgba(255,255,255,0.32)', fontFamily: 'var(--font-mono)' }}>
                  +{sel.startMs}ms → +{sel.startMs + sel.latencyMs}ms
                </span>
              </div>
              {/* span meta row */}
              <div style={{ padding: '0 14px 5px', display: 'flex', alignItems: 'center', gap: 12, fontSize: 10, color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Clock size={9} />{sel.latencyMs}ms</span>
                {sel.tokens > 0 && <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Zap size={9} />{sel.tokens} tok</span>}
                <span style={{ color: sel.status === 'success' ? '#C5DD83' : '#F8A4A4' }}>● {sel.status}</span>
              </div>
              {/* tabs */}
              <div style={{ display: 'flex', padding: '0 14px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
                {(['input', 'output', 'metadata'] as const).map(k => (
                  <button key={k} onClick={() => setTab(k)} style={{
                    background: 'transparent', border: 'none',
                    padding: '6px 10px', fontSize: 11, fontWeight: 500,
                    color: tab === k ? '#F5F5F5' : 'rgba(255,255,255,0.38)',
                    borderBottom: tab === k ? '2px solid #EBD07C' : '2px solid transparent',
                    marginBottom: -1, cursor: 'pointer', textTransform: 'capitalize',
                  }}>{k}</button>
                ))}
              </div>
              {/* tab content */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
                {tab === 'input'    && <JsonView value={sel.input} />}
                {tab === 'output'   && <JsonView value={sel.output} />}
                {tab === 'metadata' && <JsonView value={{ span_id: sel.id, node: sel.node, type: spanType(sel.node).label.toLowerCase(), status: sel.status, latency_ms: sel.latencyMs, tokens: sel.tokens, cost_usd: sel.cost }} />}
              </div>
            </>
          ) : (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.2)' }}>Select a span to inspect</p>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}

/* ─── Markdown renderer ──────────────────────────────────────── */

function renderInline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={i} style={{ fontWeight: 600, color: '#fff' }}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85em', background: 'rgba(255,255,255,0.1)', padding: '1px 5px', borderRadius: 4, color: '#EBD07C' }}>{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link)
      return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" style={{ color: '#93C5FD', textDecoration: 'underline' }}>{link[1]}</a>;
    return <span key={i}>{part}</span>;
  });
}

function MarkdownContent({ text }: { text: string }) {
  const lines = text.split('\n');
  const nodes: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Fenced code block
    if (line.startsWith('```')) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      i++;
      nodes.push(
        <pre key={`cb${i}`} style={{ background: '#0A0A0A', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '10px 14px', margin: '8px 0 4px', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'rgba(229,231,235,0.85)', overflowX: 'auto', lineHeight: 1.6, whiteSpace: 'pre' }}>
          {codeLines.join('\n')}
        </pre>
      );
      continue;
    }
    // Headers
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const lvl = hm[1].length;
      const sz = ['18px', '16px', '14px', '13px'][lvl - 1] ?? '13px';
      nodes.push(<p key={`h${i}`} style={{ fontSize: sz, fontWeight: 700, color: '#fff', margin: lvl <= 2 ? '12px 0 4px' : '8px 0 2px' }}>{renderInline(hm[2])}</p>);
      i++; continue;
    }
    // Unordered list
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s/, '')); i++; }
      nodes.push(
        <ul key={`ul${i}`} style={{ margin: '4px 0', paddingLeft: 18 }}>
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'rgba(255,255,255,0.78)' }}>{renderInline(item)}</li>)}
        </ul>
      );
      continue;
    }
    // Numbered list
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s/, '')); i++; }
      nodes.push(
        <ol key={`ol${i}`} style={{ margin: '4px 0', paddingLeft: 20 }}>
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'rgba(255,255,255,0.78)' }}>{renderInline(item)}</li>)}
        </ol>
      );
      continue;
    }
    // Blank line
    if (!line.trim()) { i++; continue; }
    // Paragraph
    nodes.push(<p key={`p${i}`} style={{ margin: '3px 0', fontSize: 13, lineHeight: 1.55 }}>{renderInline(line)}</p>);
    i++;
  }
  return <>{nodes}</>;
}

/* ─── ChatBubble ─────────────────────────────────────────────── */

function ChatBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === 'user';
  return (
    <div style={{ display: 'flex', gap: 10, flexDirection: isUser ? 'row-reverse' : 'row' }}>
      <div style={{
        width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: isUser ? 'rgba(235,208,124,0.22)' : 'rgba(255,255,255,0.1)',
      }}>
        {isUser ? <User size={13} style={{ color: '#EBD07C' }} /> : <Bot size={13} style={{ color: 'rgba(255,255,255,0.6)' }} />}
      </div>
      <div style={{ maxWidth: '78%', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{
          borderRadius: 12, padding: '10px 14px', fontSize: 13, lineHeight: 1.55,
          background: isUser ? 'rgba(235,208,124,0.18)' : 'rgba(255,255,255,0.07)',
          color: isUser ? 'rgba(255,255,255,0.92)' : 'rgba(255,255,255,0.78)',
          borderTopRightRadius: isUser ? 4 : 12,
          borderTopLeftRadius:  isUser ? 12 : 4,
        }}>
          {msg.routed && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, paddingBottom: 8, borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
              <Route size={11} style={{ color: '#F0B429' }} />
              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)' }}>Router →</span>
              <span style={{ fontSize: 10, fontWeight: 700, color: '#F0B429' }}>{msg.routed.name}</span>
              <span style={{ marginLeft: 'auto', fontSize: 9.5, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.38)' }}>
                conf {(msg.routed.conf * 100).toFixed(0)}%
              </span>
            </div>
          )}
          {isUser
            ? <p style={{ margin: 0 }}>{msg.content}</p>
            : <MarkdownContent text={msg.content} />
          }
          <p style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.25)', marginTop: 6 }}>{msg.timestamp}</p>
        </div>
      </div>
    </div>
  );
}

/* ─── Main component ─────────────────────────────────────────── */

export default function AgentPlayground({ embedded = false }: { embedded?: boolean }) {
  const [mode, setMode]           = useState<'unified' | 'single'>('single');
  const [debugOn, setDebugOn]     = useState(true);
  const [agents, setAgents]       = useState<AgentOption[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  // blank by default — user must pick an agent before sending
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectorOpen, setSelectorOpen]   = useState(false);
  const [messages, setMessages]   = useState<Message[]>([]);
  const [input, setInput]         = useState('');
  const [sending, setSending]     = useState(false);
  const [traceSteps, setTraceSteps]   = useState<TraceStep[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [pendingOptions, setPendingOptions]     = useState<string[] | null>(null);
  const [pendingApproval, setPendingApproval]   = useState<PendingApproval | null>(null);
  const [approvalNote, setApprovalNote]         = useState('');
  const [approvalLoading, setApprovalLoading]   = useState(false);
  const [customOptionInput, setCustomOptionInput] = useState('');

  useEffect(() => { if (!pendingOptions) setCustomOptionInput(''); }, [pendingOptions]);


  const bottomRef   = useRef<HTMLDivElement>(null);
  const selectorRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  };

  /* Close selector on outside click */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node))
        setSelectorOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  /* Load agents — no auto-select */
  useEffect(() => {
    (async () => {
      try {
        const data = await apiGet<{ id: string; name: string; description: string; routing_keywords?: string[] }[]>('/api/catalog/agents');
        const opts: AgentOption[] = data.map(a => ({
          id: a.id, label: a.name, description: a.description,
          routing_keywords: a.routing_keywords,
        }));
        setAgents(opts);
        // intentionally NOT calling setSelectedAgent here — user picks manually
      } catch {
        /* backend not running */
      } finally {
        setAgentsLoading(false);
      }
    })();
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  /* ── canSend ── */
  const canSend = input.trim().length > 0 && !sending && (mode === 'unified' || !!selectedAgent);

  /* ── Send Single ── */
  const handleSendSingle = async (text: string) => {
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const userMsgId = Date.now().toString();
    const placeholderId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: userMsgId, role: 'user', content: text, timestamp: now }]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSending(true);
    setMessages(prev => [...prev, { id: placeholderId, role: 'assistant', content: '', timestamp: now }]);

    try {
      const res = await fetch(`${API_URL}/api/conversations/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(keycloak?.token ? { Authorization: `Bearer ${keycloak.token}` } : {}) },
        body: JSON.stringify({
          query: text, agent_id: selectedAgent,
          conversation_id: conversationId,
          messages: messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
        }),
      });

      const convId = res.headers.get('X-Conversation-Id');
      if (convId && !conversationId) setConversationId(convId);

      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();
      let fullText  = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          if (data.startsWith('[ERROR]')) {
            fullText = data.slice(7).trim() || 'Đã xảy ra lỗi, vui lòng thử lại.';
            setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: fullText } : m));
            break;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed?.__suggestions) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, suggestions: parsed.__suggestions } : m));
            } else if (parsed?.__options) {
              setPendingOptions(parsed.__options);
            } else if (parsed?.__pending_review) {
              setMessages(prev => prev.map(m => m.id === placeholderId
                ? { ...m, content: '' }
                : m,
              ));
              setPendingApproval({
                convId:        parsed.__pending_review.conv_id ?? parsed.__pending_review,
                placeholderId,
                payload:       parsed.__pending_review.payload ?? null,
              });
            } else {
              fullText += parsed;
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: fullText } : m));
            }
          } catch {
            fullText += data;
            setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: fullText } : m));
          }
        }
      }

      if (convId) {
        setTraceLoading(true);
        (async () => {
          for (let i = 0; i < 5; i++) {
            try {
              const { observations, traceId: obsTraceId } = await apiGet<any>(`/api/traces/${convId}`);
              const steps = mapObservations(observations ?? [], obsTraceId);
              if (steps.length > 0) { setTraceSteps(steps); break; }
            } catch { /* ignore */ }
            if (i < 4) await new Promise(r => setTimeout(r, 2000));
          }
          setTraceLoading(false);
        })();
      }
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, content: 'Lỗi: không kết nối được backend.' } : m,
      ));
    } finally {
      setSending(false);
    }
  };

  /* ── Build manual trace steps from orchestrator response (fallback khi LangFuse không có) ── */
  function buildManualSteps(data: any): TraceStep[] {
    const plannerMs  = data.planner_ms   ?? 0;
    const executorMs = data.executor_ms  ?? 0;
    const synthMs    = data.synthesizer_ms ?? 0;
    const steps: TraceStep[] = [];

    steps.push({
      id: 'orchestrator-planner', node: 'orchestrator-planner', status: 'success',
      startMs: 0, latencyMs: plannerMs, tokens: 0, cost: 0,
      input: JSON.stringify({ query: data.user_input ?? '' }, null, 2),
      output: `Plan created (${(data.target_results ?? []).length} targets)`,
      iteration: 0, isRetryStart: false,
    });

    for (const target of (data.target_results ?? [])) {
      const targetStart = plannerMs + Math.min(...(target.task_results ?? [{ start_ms: 0 }]).map((t: any) => t.start_ms ?? 0));
      steps.push({
        id: target.node_id, node: `[target] ${target.description.slice(0, 80)}`,
        status: target.success ? 'success' : 'error',
        startMs: targetStart, latencyMs: 0, tokens: 0, cost: 0,
        input: JSON.stringify({ description: target.description }, null, 2),
        output: target.success ? 'OK' : `Failed: ${target.failed_task}`,
        iteration: 0, isRetryStart: false,
      });
      const targetHeaderIdx = steps.length - 1;
      const taskOutputs: string[] = [];
      let targetEnd = targetStart;

      for (const task of (target.task_results ?? [])) {
        const taskStartMs = plannerMs + (task.start_ms ?? 0);
        const taskLatency = task.latency_ms ?? 0;
        steps.push({
          id: `${target.node_id}-${task.task_index}`, node: `[task] ${task.task_title}`,
          status: task.success ? 'success' : 'error',
          startMs: taskStartMs, latencyMs: taskLatency, tokens: 0, cost: 0,
          input: JSON.stringify({ agent_id: task.agent_id, agent_name: task.agent_name, task: task.task_title }, null, 2),
          output: task.success ? (task.output || '(empty)') : (task.error ?? '(failed)'),
          iteration: 0, isRetryStart: false,
          agentConvId: task.conversation_id ?? undefined,
          agentLfTraceId: task.lf_trace_id ?? undefined,
          agentLfSpanId: task.lf_span_id ?? undefined,
        });
        if (task.success && task.output) taskOutputs.push(`[${task.task_title}]: ${task.output}`);
        targetEnd = Math.max(targetEnd, taskStartMs + taskLatency);
      }
      steps[targetHeaderIdx].latencyMs = targetEnd - targetStart;
      steps[targetHeaderIdx].output = target.success ? (taskOutputs.join('\n\n') || 'Completed') : `Failed: ${target.failed_task}`;
    }

    steps.push({
      id: 'orchestrator-synthesizer', node: 'orchestrator-synthesizer', status: 'success',
      startMs: plannerMs + executorMs, latencyMs: synthMs, tokens: 0, cost: 0,
      input: 'Final outputs from all targets', output: data.response ?? '',
      iteration: 0, isRetryStart: false,
    });
    return steps;
  }

  /* ── Send Orchestrator ── */
  /* ── Send Unified (Orchestrator) ── */
  const handleSendUnified = async (text: string) => {
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const userMsgId = Date.now().toString();
    const placeholderId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: userMsgId, role: 'user', content: text, timestamp: now }]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSending(true);
    setTraceLoading(true);
    setMessages(prev => [...prev, { id: placeholderId, role: 'assistant', content: '', timestamp: now }]);

    try {
      const res = await fetch(`${API_URL}/api/orchestrate/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(keycloak?.token ? { Authorization: `Bearer ${keycloak.token}` } : {}) },
        body: JSON.stringify({ query: text, user_id: keycloak?.tokenParsed?.sub ?? 'playground' }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      setMessages(prev => prev.map(m =>
        m.id === placeholderId
          ? { ...m, content: data.response ?? '', suggestions: data.suggestions ?? [] }
          : m,
      ));

      // Build lookup maps từ target_results — dùng để restore agentConvId + lf context sau mapObservations
      const convIdMap  = new Map<string, string>();
      const lfSpanMap  = new Map<string, { convId: string; lfTraceId: string; lfSpanId: string }>();
      for (const target of (data.target_results ?? [])) {
        for (const task of (target.task_results ?? [])) {
          if (task.conversation_id) convIdMap.set(task.task_title, task.conversation_id);
          if (task.lf_span_id && task.lf_trace_id && task.conversation_id) {
            lfSpanMap.set(task.lf_span_id, {
              convId:    task.conversation_id,
              lfTraceId: task.lf_trace_id,
              lfSpanId:  task.lf_span_id,
            });
          }
        }
      }
      const injectConvIds = (steps: TraceStep[]) =>
        steps.map(s => {
          // Inject theo lf_span_id (khớp chính xác, hoạt động cả [task] và single-agent)
          if (s.agentLfSpanId && lfSpanMap.has(s.agentLfSpanId)) {
            const info = lfSpanMap.get(s.agentLfSpanId)!;
            return { ...s, agentConvId: s.agentConvId ?? info.convId, agentLfTraceId: info.lfTraceId, agentLfSpanId: info.lfSpanId };
          }
          // Fallback: inject agentConvId theo task title cho [task] spans
          if (s.node.startsWith('[task]') && !s.agentConvId) {
            const title = s.node.replace(/^\[task\]\s*/, '').replace(/#\d+$/, '');
            const convId = convIdMap.get(title);
            return convId ? { ...s, agentConvId: convId } : s;
          }
          return s;
        });

      // Hiển thị manual steps ngay lập tức (fast path, không có timing/tokens/cost)
      // Sau đó cập nhật lại bằng dữ liệu Langfuse (rich: real timing + tokens + cost)
      const sessionId = data.session_id;
      setTraceSteps(buildManualSteps(data));
      if (sessionId) {
        setConversationId(sessionId);
        (async () => {
          for (let i = 0; i < 6; i++) {
            await new Promise(r => setTimeout(r, 2000));
            try {
              const { observations, traceId: obsTraceId } = await apiGet<any>(`/api/traces/${sessionId}`);
              const mapped = mapObservations(observations ?? [], obsTraceId);
              if (mapped.length > 0) { setTraceSteps(injectConvIds(mapped)); return; }
            } catch { break; }
          }
        })();
      }
    } catch (e: any) {
      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, content: `Lỗi: ${e.message}` } : m,
      ));
    } finally {
      setSending(false);
      setTraceLoading(false);
    }
  };

  const handleApproval = async (approved: boolean) => {
    if (!pendingApproval || approvalLoading) return;
    setApprovalLoading(true);
    const { convId, placeholderId } = pendingApproval;
    try {
      const data = await apiMutate<any>('POST', `/api/reviews/${convId}/resume`, { approved, note: approvalNote });
      const responseText = data.response || (approved ? '' : 'Yêu cầu không được phê duyệt.');
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: responseText } : m));
    } catch {
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: 'Lỗi khi xử lý phê duyệt.' } : m));
    } finally {
      setPendingApproval(null);
      setApprovalNote('');
      setApprovalLoading(false);
    }
  };

  const handleSend = (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || sending) return;
    setPendingOptions(null);
    if (mode === 'unified') { handleSendUnified(text); return; }
    if (!selectedAgent) return;
    handleSendSingle(text);
  };

  /* ── Derived ── */
  const selectedAgentObj = agents.find(a => a.id === selectedAgent);

  /* ── Send button dimming logic ──
     - no agent selected + single mode → show hint via placeholder, button dim
     - typing but no agent → still dim, don't block typing
  */
  const sendBtnActive = canSend;

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      height: embedded ? 'calc(100vh - 320px)' : 'calc(100vh - 200px)',
      minHeight: 520,
      borderRadius: 14, border: '1px solid rgba(255,255,255,0.1)',
      overflow: 'hidden', background: '#0D0D0D',
    }}>

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', borderBottom: '1px solid rgba(255,255,255,0.1)', flexShrink: 0, gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <MessageSquare size={15} style={{ color: '#EBD07C' }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.8)' }}>Playground</span>
          </div>
          <div style={{ display: 'flex', gap: 2, padding: 3, background: 'rgba(255,255,255,0.06)', borderRadius: 9, border: '1px solid rgba(255,255,255,0.1)' }}>
            {([['unified', 'Unified Chat'], ['single', 'Single Agent']] as const).map(([id, label]) => (
              <button key={id} onClick={() => { setMode(id); setMessages([]); setTraceSteps([]); }} style={{
                padding: '5px 11px', borderRadius: 7, fontSize: 11, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: mode === id ? 'rgba(235,208,124,0.18)' : 'transparent',
                color: mode === id ? '#EBD07C' : 'rgba(255,255,255,0.4)',
              }}>{label}</button>
            ))}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Agent selector */}
          {mode === 'single' && (
            <div ref={selectorRef} style={{ position: 'relative' }}>
              <button
                onClick={() => setSelectorOpen(v => !v)}
                disabled={agentsLoading}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: selectedAgent ? 'rgba(235,208,124,0.10)' : 'rgba(255,255,255,0.06)',
                  border: '1px solid ' + (selectedAgent ? 'rgba(235,208,124,0.35)' : 'rgba(255,255,255,0.1)'),
                  color: selectedAgent ? '#EBD07C' : 'rgba(255,255,255,0.35)',
                  fontSize: 11.5, borderRadius: 9,
                  padding: '6px 10px', minWidth: 196, cursor: 'pointer',
                }}
              >
                <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {agentsLoading ? 'Loading…' : (selectedAgentObj?.label ?? 'Select an agent…')}
                </span>
                <ChevronDown size={12} style={{ color: selectedAgent ? '#EBD07C' : 'rgba(255,255,255,0.3)', transform: selectorOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
              </button>
              <AnimatePresence>
                {selectorOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.12 }}
                    style={{ position: 'absolute', right: 0, top: '100%', marginTop: 4, width: 240, background: '#1C1C1C', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.4)', zIndex: 50, padding: '6px 0' }}
                  >
                    <div style={{ padding: '5px 12px 2px', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'rgba(255,255,255,0.3)' }}>Agents</div>
                    {agents.length === 0 && <p style={{ padding: '8px 12px', fontSize: 12, color: 'rgba(255,255,255,0.3)' }}>No agents found</p>}
                    {agents.map(a => (
                      <button key={a.id}
                        onClick={() => { setSelectedAgent(a.id); setSelectorOpen(false); setConversationId(null); setMessages([]); setPendingOptions(null); }}
                        style={{
                          width: '100%', textAlign: 'left', padding: '8px 12px', fontSize: 11.5,
                          background: selectedAgent === a.id ? 'rgba(235,208,124,0.15)' : 'transparent',
                          color: selectedAgent === a.id ? '#EBD07C' : 'rgba(255,255,255,0.6)',
                          border: 'none', cursor: 'pointer',
                        }}
                        onMouseEnter={e => { if (selectedAgent !== a.id) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)'; }}
                        onMouseLeave={e => { if (selectedAgent !== a.id) (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                      >{a.label}</button>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}
          {mode === 'unified' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: 'rgba(192,132,252,0.12)', border: '1px solid rgba(192,132,252,0.4)', borderRadius: 9, padding: '6px 11px' }}>
              <Brain size={13} style={{ color: '#C084FC' }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: '#C084FC' }}>Multi-agent orchestrator</span>
            </div>
          )}
          <button
            onClick={() => setDebugOn(v => !v)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5,
              padding: '6px 11px', borderRadius: 9, cursor: 'pointer',
              border: '1px solid ' + (debugOn ? 'rgba(235,208,124,0.5)' : 'rgba(255,255,255,0.1)'),
              background: debugOn ? 'rgba(235,208,124,0.15)' : 'rgba(255,255,255,0.04)',
              color: debugOn ? '#EBD07C' : 'rgba(255,255,255,0.4)',
            }}
          >
            <Bug size={12} /> Debug {debugOn ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>

        {/* Chat panel */}
        <motion.div
          animate={{ width: debugOn ? '55%' : '100%' }}
          transition={{ duration: 0.25, ease: 'easeInOut' }}
          style={{ display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'hidden', borderRight: debugOn ? '1px solid rgba(255,255,255,0.1)' : 'none' }}
        >
          {/* Context badge */}
          <div style={{ padding: '7px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>
              {mode === 'unified'
                ? <><Brain size={11} style={{ color: '#C084FC' }} /><span style={{ color: 'rgba(192,132,252,0.7)' }}>Orchestrator — ToT planner assigns agents automatically</span></>
                : selectedAgentObj
                  ? <><Bot size={11} />{selectedAgentObj.label}</>
                  : <><Bot size={11} style={{ opacity: 0.4 }} /><span style={{ color: 'rgba(255,255,255,0.25)' }}>No agent selected</span></>
              }
            </span>
          </div>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '14px 14px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Empty state when no agent selected */}
            {mode === 'single' && !selectedAgent && messages.length === 0 && (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, opacity: 0.5 }}>
                <Bot size={28} style={{ color: 'rgba(255,255,255,0.2)' }} />
                <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', textAlign: 'center', margin: 0 }}>
                  Select an agent from the dropdown<br />to start a conversation
                </p>
              </div>
            )}
            <AnimatePresence initial={false}>
              {messages.map(msg => {
                if (msg.content === '' && msg.role === 'assistant') {
                  return (
                    <motion.div key={msg.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ display: 'flex', gap: 10 }}>
                      <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'rgba(255,255,255,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <Bot size={13} style={{ color: 'rgba(255,255,255,0.6)' }} />
                      </div>
                      <div style={{ background: 'rgba(255,255,255,0.07)', borderRadius: 12, borderTopLeftRadius: 4, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 5 }}>
                        {[0, 1, 2].map(i => (
                          <span key={i} className="pulse-soft" style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(255,255,255,0.3)', display: 'inline-block', animationDelay: i * 0.18 + 's' }} />
                        ))}
                      </div>
                    </motion.div>
                  );
                }
                return <ChatBubble key={msg.id} msg={msg} />;
              })}
            </AnimatePresence>
            <div ref={bottomRef} />
          </div>

          {/* Suggestion chips */}
          {(() => {
            const last = messages.length > 0 ? messages[messages.length - 1] : null;
            const chips = !sending && last?.role === 'assistant' && last.suggestions?.length ? last.suggestions : null;
            if (!chips) return null;
            return (
              <div style={{ padding: '0 14px 8px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {chips.map((s, i) => (
                  <button key={i} onClick={() => handleSend(s)} style={{
                    fontSize: 11, padding: '5px 11px', borderRadius: 9999,
                    background: 'rgba(235,208,124,0.10)', border: '1px solid rgba(235,208,124,0.3)',
                    color: '#EBD07C', cursor: 'pointer',
                  }}>
                    <Sparkles size={9} style={{ verticalAlign: '-1px', marginRight: 4 }} />{s}
                  </button>
                ))}
              </div>
            );
          })()}

          {/* Options popup */}
          <AnimatePresence>
            {pendingOptions && (
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                style={{ margin: '0 12px 8px', background: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, overflow: 'hidden' }}>
                <p style={{ padding: '10px 14px 6px', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'rgba(255,255,255,0.3)', margin: 0 }}>
                  Chọn hướng tiếp cận
                </p>
                <div style={{ padding: '0 8px 8px', display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {pendingOptions.map((opt, i) => (
                    <button key={i} onClick={() => { setPendingOptions(null); handleSend(opt); }}
                      style={{ width: '100%', textAlign: 'left', padding: '8px 10px', borderRadius: 8, border: '1px solid rgba(235,208,124,0.2)', background: 'rgba(235,208,124,0.07)', color: '#EBD07C', fontSize: 13, cursor: 'pointer' }}>
                      {opt}
                    </button>
                  ))}
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '6px 8px', marginTop: 2 }}>
                    <textarea
                      value={customOptionInput}
                      onChange={e => setCustomOptionInput(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          if (customOptionInput.trim()) { setCustomOptionInput(''); handleSend(customOptionInput.trim()); }
                        }
                      }}
                      placeholder="Tự nhập câu trả lời..."
                      rows={1}
                      style={{ flex: 1, background: 'transparent', fontSize: 13, color: 'rgba(255,255,255,0.65)', resize: 'none', outline: 'none', border: 'none', lineHeight: 1.5, fontFamily: 'inherit', fontStyle: 'italic' }}
                    />
                    <button
                      onClick={() => { if (customOptionInput.trim()) { setCustomOptionInput(''); handleSend(customOptionInput.trim()); } }}
                      disabled={!customOptionInput.trim() || sending}
                      style={{ flexShrink: 0, width: 26, height: 26, borderRadius: 7, background: customOptionInput.trim() ? '#B88719' : 'rgba(184,135,25,0.2)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: customOptionInput.trim() ? 'pointer' : 'default', color: '#fff' }}
                    >
                      <Send size={11} />
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Human Approval card */}
          <AnimatePresence>
            {pendingApproval && (
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }}
                style={{ margin: '0 12px 8px', background: '#1a1a1a', border: '1px solid rgba(217,184,108,0.35)', borderRadius: 12, overflow: 'hidden' }}>
                <div style={{ padding: '10px 14px 6px', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#D9B86C', flexShrink: 0 }} />
                  <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#D9B86C', fontWeight: 700 }}>
                    Cần phê duyệt
                  </span>
                </div>
                <div style={{ padding: '4px 14px 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {pendingApproval.payload?.title && (
                    <div style={{ fontSize: 12.5, fontWeight: 600, color: '#fff' }}>
                      {pendingApproval.payload.title}
                    </div>
                  )}
                  {pendingApproval.payload?.description && (
                    <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.6)', lineHeight: 1.5, fontFamily: 'var(--font-mono)', background: 'rgba(255,255,255,0.04)', borderRadius: 6, padding: '7px 10px' }}>
                      {pendingApproval.payload.description}
                    </div>
                  )}
                  <textarea
                    value={approvalNote}
                    onChange={e => setApprovalNote(e.target.value)}
                    placeholder="Ghi chú (tuỳ chọn)..."
                    rows={1}
                    style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 6, padding: '6px 9px', fontSize: 11.5, color: 'rgba(255,255,255,0.65)', fontFamily: 'inherit', resize: 'none', outline: 'none' }}
                  />
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      disabled={approvalLoading}
                      onClick={() => handleApproval(false)}
                      style={{ flex: 1, padding: '7px 0', borderRadius: 7, border: '1px solid rgba(248,113,113,0.3)', background: 'rgba(248,113,113,0.1)', color: '#F87171', fontSize: 12, fontWeight: 600, cursor: 'pointer', opacity: approvalLoading ? 0.5 : 1 }}>
                      Từ chối
                    </button>
                    <button
                      disabled={approvalLoading}
                      onClick={() => handleApproval(true)}
                      style={{ flex: 1, padding: '7px 0', borderRadius: 7, border: '1px solid rgba(52,211,153,0.35)', background: 'rgba(52,211,153,0.12)', color: '#34D399', fontSize: 12, fontWeight: 600, cursor: 'pointer', opacity: approvalLoading ? 0.5 : 1 }}>
                      {approvalLoading ? 'Đang xử lý...' : 'Phê duyệt ✓'}
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Input */}
          {!pendingOptions && !pendingApproval && <div style={{ padding: '8px 12px 14px', flexShrink: 0 }}>
            {/* hint when no agent selected */}
            {mode === 'single' && !selectedAgent && (
              <p style={{ fontSize: 10.5, color: 'rgba(255,255,255,0.25)', textAlign: 'center', margin: '0 0 6px', fontStyle: 'italic' }}>
                Choose an agent above to enable sending
              </p>
            )}
            <div style={{
              display: 'flex', gap: 8, alignItems: 'center',
              background: 'rgba(255,255,255,0.05)',
              border: '1px solid ' + (mode === 'single' && !selectedAgent ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.1)'),
              borderRadius: 12, padding: '8px 10px',
              opacity: mode === 'single' && !selectedAgent ? 0.6 : 1,
            }}>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                placeholder={
                  mode === 'unified'
                    ? 'Ask anything — orchestrator plans & assigns agents…'
                    : !selectedAgent
                      ? 'Select an agent first…'
                      : 'Ask the agent anything… (Enter to send)'
                }
                rows={1}
                style={{ flex: 1, background: 'transparent', fontSize: 13, color: 'rgba(255,255,255,0.8)', resize: 'none', outline: 'none', border: 'none', lineHeight: 1.55, fontFamily: 'inherit', maxHeight: 120, overflowY: 'auto' }}
              />
              <button
                onClick={() => handleSend()}
                disabled={!sendBtnActive}
                style={{
                  flexShrink: 0, width: 32, height: 32, borderRadius: 9,
                  background: sendBtnActive ? '#B88719' : 'rgba(184,135,25,0.25)',
                  border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: sendBtnActive ? 'pointer' : 'not-allowed', color: '#fff',
                }}
              >
                <Send size={13} />
              </button>
            </div>
          </div>}
        </motion.div>

        {/* Trace panel — slides in from right, out to right */}
        <AnimatePresence initial={false}>
          {debugOn && (
            <motion.div
              key="trace"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ duration: 0.25, ease: 'easeInOut' }}
              style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: '45%', display: 'flex', overflow: 'hidden' }}
            >
              <TracePanel steps={traceSteps} traceLoading={traceLoading} traceId={conversationId} />
            </motion.div>
          )}
        </AnimatePresence>

      </div>
    </div>
  );
}