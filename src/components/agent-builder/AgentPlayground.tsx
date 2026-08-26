import React, { useState, useRef, useEffect } from 'react';
import {
  MessageSquare, Send, Bug, ChevronDown,
  Clock, Zap, Bot, User, Layers, Search,
  Brain, FileOutput, AlertCircle,
  Route, Sparkles, Copy, GripHorizontal,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, AreaChart, Area,
  ScatterChart, Scatter, RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  RadialBarChart, RadialBar, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
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
  startAbsMs: number;       // absolute epoch ms, dùng để tính offset chính xác cho inner spans
  tokens: number;
  cost: number;
  input: string;
  output: string;
  iteration: number;
  isRetryStart: boolean;
  isConcurrentBranch: boolean;  // true = span cùng tên trước đó vẫn CHƯA kết thúc (chạy song song thật, vd ToT branching) — khác "retry" (chạy sau khi cái trước đã xong, vd judge loop)
  verdict?: 'PASS' | 'FAIL';
  errorType?: string;
  agentConvId?: string;
  agentLfTraceId?: string;
  agentLfSpanId?: string;
  agentGroupId?: string;        // multi-agent: conv_id của agent group này thuộc về
  isAgentGroupHeader?: boolean; // true = dòng tổng của 1 agent trong multi-agent timeline
}

interface ChartSpec {
  chart_type: 'bar' | 'line' | 'area' | 'pie' | 'scatter' | 'radar' | 'radialBar' | 'composed';
  title: string;
  x_key: string;
  y_keys: string[];
  data: Record<string, unknown>[];
  series?: Array<{ key: string; type: 'bar' | 'line' | 'area' }>;  // chỉ dùng khi chart_type === 'composed'
}

interface Citation {
  index: number;
  source: string;
  type: 'kb' | 'mcp' | 'db' | 'web' | 'tool';
  score?: number | null;
  snippet: string;
  url?: string | null;
  doc_id?: string | null;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  suggestions?: string[];
  routed?: { name: string; conf: number } | null;
  chartSpecs?: ChartSpec[];
  citations?: Citation[];
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
    return { label: 'TARGET',     color: '#C084FC', bg: 'rgba(192,132,252,0.14)' };
  if (node.startsWith('[task]'))
    return { label: 'TASK',       color: '#67E8F9', bg: 'rgba(103,232,249,0.12)' };
  if (node.startsWith('[agent]'))
    return { label: 'AGENT',      color: '#A78BFA', bg: 'rgba(167,139,250,0.18)' };
  if (node === 'single-agent')
    return { label: 'AGENT',      color: '#A78BFA', bg: 'rgba(167,139,250,0.12)' };
  if (node === 'router')
    return { label: 'ROUTING',    color: '#F0B429', bg: 'rgba(240,180,41,0.18)' };
  if (node.includes('guardrail'))
    return { label: 'GUARD',      color: '#F8A4A4', bg: 'rgba(248,164,164,0.14)' };
  if (node === 'orchestrator-synthesizer')
    return { label: 'SYNTH',      color: '#86EFAC', bg: 'rgba(134,239,172,0.12)' };
  if (node.includes('planner') || node.includes('responder') || node.includes('suggestion') || node.includes('input_refine') || node.includes('input-refine') || node.includes('summary'))
    return { label: 'GENERATION', color: '#EBD07C', bg: 'rgba(235,208,124,0.14)' };
  if (node.includes('kb_search'))
    return { label: 'RETRIEVAL',  color: '#93C5FD', bg: 'rgba(147,197,253,0.12)' };
  if (node.includes('mcp') || node.includes('tool_executor') || node.startsWith('tool/'))
    return { label: 'TOOL',       color: '#86EFAC', bg: 'rgba(134,239,172,0.12)' };
  if (node.includes('rrf') || node.includes('reranker'))
    return { label: 'RANK',       color: '#F3E2A7', bg: 'rgba(243,226,167,0.10)' };
  if (node.includes('judge'))
    return { label: 'EVAL',       color: '#C084FC', bg: 'rgba(192,132,252,0.14)' };
  if (node.includes('human_approval'))
    return { label: 'APPROVAL',   color: '#7DD3FC', bg: 'rgba(125,211,252,0.14)' };
  return   { label: 'NODE',       color: '#BFA66A', bg: 'rgba(191,166,106,0.12)' };
}

/* ─── Helpers ────────────────────────────────────────────────── */

const _SKIP_NODES = new Set(['LangGraph', 'join_retrieval', 'join_end', 'ChatOpenAI']);
const _HIDE_SPAN  = new Set(['orchestrator']); // hide span itself but keep children visible
const _shouldSkip = (name: string) => _SKIP_NODES.has(name) || name.startsWith('_route') || name.startsWith('route_');

// Span hạ tầng OTEL tự động (postgres connect, mongo ping/find, wrapper HTTP
// request của FastAPI/httpx instrumentation...) — không phải node thật của
// app, chỉ gây nhiễu waterfall. Nhận diện qua scope.name trong metadata
// (opentelemetry.instrumentation.* — khác với span thật của app luôn có
// scope.name = "langfuse-sdk" hoặc không có scope).
function _isInfraSpan(o: any): boolean {
  const scopeName = o?.metadata?.scope?.name as string | undefined;
  return !!scopeName && scopeName.startsWith('opentelemetry.instrumentation.');
}

// "LangGraph" là tên mặc định của CallbackHandler cho MỌI graph invocation —
// dùng chung ở CẢ orchestrator (bọc guardrail/input_refine/single_agent/...)
// LẪN workflow-runtime (bọc planner/judge/tool_executor/... của 1 agent run).
// Chỉ span của workflow-runtime mới cần ẩn con (chờ user expand single-agent/
// [task]) — span của orchestrator là gốc thật, con của nó luôn phải hiện
// ngay. Phân biệt qua service.name trong resourceAttributes (OTEL), KHÔNG
// thể phân biệt bằng tên vì cả 2 đều tên "LangGraph".
function _isAgentInternalWrapper(o: any): boolean {
  return o?.name === 'LangGraph' &&
    o?.metadata?.resourceAttributes?.['service.name'] === 'workflow-runtime';
}

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
      return JSON.stringify({ query: q }, null, 2);
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
  // Build parent lookup (full object, không chỉ tên) — cần cả metadata để
  // phân biệt "LangGraph" của orchestrator vs của workflow-runtime.
  const byId = new Map<string, any>(obs.map(o => [o.id as string, o]));
  const infraById = new Map<string, boolean>(obs.map(o => [o.id as string, _isInfraSpan(o)]));

  // Detect context to avoid mixing orchestrator-level and agent-internal spans:
  // - outer multi-agent trace  : has [task] spans → hasTaskSpan = true
  // - outer single-agent trace : has 'single-agent' span → hasSingleAgentSpan = true
  // - expanded agent view      : neither flag → include all visible spans
  const names = obs.map(o => o.name as string);
  const hasTaskSpan        = names.some(n => n?.startsWith('[task]'));
  const hasSingleAgentSpan = names.some(n => n === 'single-agent');
  // Either outer-trace context → exclude agent-internal spans (behind skipped LangGraph wrapper)
  const isOuterTrace = hasTaskSpan || hasSingleAgentSpan;

  const byTime = obs
    .filter(o => {
      if (_isInfraSpan(o)) return false;
      if (_shouldSkip(o.name) || _HIDE_SPAN.has(o.name)) return false;
      // level=DEBUG: node có ý nghĩa quan sát thấp trên happy path (vd
      // judge_phase1 khi PASS — services/workflow-runtime/graph.py hạ level
      // xuống DEBUG lúc không bounce). Ẩn khỏi trace mặc định, đúng ý nghĩa
      // chuẩn của field "level" — không cần bật riêng cho từng tên node.
      if (o.level === 'DEBUG') return false;
      // LƯU Ý: từng có `if (o.name?.startsWith('judge')) return true;` ở đây
      // (thời filter còn ràng buộc type==='SPAN', judge là GENERATION/CHAIN
      // nên cần miễn trừ riêng). Đã bỏ — nó khiến judge/judge_phase1 bỏ qua
      // luôn rule ẩn-con-của-LangGraph-wrapper bên dưới, nên hiện ĐỒNG THỜI
      // ở cả outer trace lẫn bên trong single-agent đã expand (cùng 1 id →
      // click 1 cái thì cả 2 cùng highlight). Giờ filter không còn ràng buộc
      // type==='SPAN' nữa (chỉ còn ở nhánh fallback không có parent trong
      // set), nên judge/judge_phase1 cứ để rơi xuống rule parent-hierarchy
      // như mọi node khác (planner/kb_search/...) là đủ, không cần miễn trừ.
      // Ẩn mọi span rơi vào tag mặc định "NODE" (spanType() không nhận diện
      // được) — đây là các node hạ tầng/định tuyến không có giá trị hiển thị
      // (vd fetch_agents, execute, single_agent wrapper) — khác "single-agent"
      // (gạch ngang, tag AGENT, span thủ công) nên KHÔNG bị ẩn nhầm.
      if (spanType(o.name).label === 'NODE') return false;
      const parentId = o.parentObservationId as string | undefined;
      const parentObs = parentId ? byId.get(parentId) : undefined;
      const parentName = parentObs?.name as string | undefined;
      // Parent not in this observation set (external root) → include only SPANs
      if (!parentId || parentName === undefined) return o.type === 'SPAN';
      // Parent là "LangGraph" của workflow-runtime (wrapper nội bộ 1 agent run)
      // → ẩn con, chờ user expand single-agent/[task]. "LangGraph" của
      // orchestrator (wrapper gốc, bọc guardrail/input_refine/...) → LUÔN
      // hiện con, không quan tâm isOuterTrace (đây chính là các bước top-level
      // thật sự, không phải chi tiết ẩn bên trong 1 agent).
      if (parentName === 'LangGraph') {
        return _isAgentInternalWrapper(parentObs) ? !isOuterTrace : true;
      }
      // Parent là skip/infra node khác (join_retrieval, ChatOpenAI, route/_route,
      // FastAPI/httpx auto-instrumentation POST span) — "promote" con lên 1 cấp:
      //   outer trace   → exclude (sẽ hiện khi expand [task]/single-agent)
      //   expanded view → include (đây là các span top-level thật của agent)
      if (_shouldSkip(parentName) || infraById.get(parentId)) return !isOuterTrace;
      // Parent exists and is not skipped → always include
      return true;
    })
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
  // Theo dõi endTime của lần xuất hiện GẦN NHẤT theo từng tên — để phân biệt
  // "chạy song song thật" (vd nhiều candidate của ToT planner qua asyncio.gather)
  // với "chạy tuần tự sau khi cái trước đã xong" (vd judge loop thất bại → thử lại).
  const lastEndByName: Record<string, number> = {};
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
    const absMs = new Date(o.startTime).getTime();
    const latencyMs = o.endTime ? Math.round(new Date(o.endTime).getTime() - absMs) : 0;
    const endAbsMs = absMs + latencyMs;
    const prevEnd = lastEndByName[o.name];
    const isConcurrentBranch = isRetryStart && prevEnd !== undefined && absMs < prevEnd;
    lastEndByName[o.name] = Math.max(prevEnd ?? 0, endAbsMs);
    return {
      id: o.id,
      node: displayName,
      status: o.level === 'ERROR' ? 'error' : 'success',
      startMs: Math.round(absMs - traceStart),
      startAbsMs: absMs,
      latencyMs,
      tokens: o.usage ? ((o.usage.input ?? 0) + (o.usage.output ?? 0)) : 0,
      cost: o.calculatedTotalCost ?? 0,
      input: trimInput(o.name, o.input),
      output: o.level === 'ERROR' && o.statusMessage
        ? o.statusMessage
        : typeof o.output === 'object' ? JSON.stringify(o.output, null, 2) : String(o.output ?? ''),
      iteration,
      isRetryStart,
      isConcurrentBranch,
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
  const displayNode = step.node.replace(/^\[(target|task|agent)\]\s*/, '');
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

function getDescendants(obs: any[], parentId: string): any[] {
  const childrenMap = new Map<string, any[]>();
  for (const o of obs) {
    const pid = o.parentObservationId ?? '__root__';
    if (!childrenMap.has(pid)) childrenMap.set(pid, []);
    childrenMap.get(pid)!.push(o);
  }
  const result: any[] = [];
  const queue = [parentId];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const kid of childrenMap.get(cur) ?? []) {
      result.push(kid);
      if (kid.id) queue.push(kid.id);
    }
  }
  return result;
}

function TracePanel({ steps, traceLoading, traceId, rawObservations }: {
  steps: TraceStep[]; traceLoading: boolean; traceId: string | null; rawObservations: any[];
}) {
  const [tab, setTab]           = useState<'input' | 'output' | 'metadata'>('input');
  // Track by ID instead of index to prevent index-shift when expanding rows
  const [selectedId, setSelectedId] = useState<string>('');
  const [splitPct, setSplitPct] = useState(40);
  const isDragging    = useRef(false);
  const containerRef  = useRef<HTMLDivElement>(null);

  // expand state cho task rows (single-agent inline expand)
  const [expanded,       setExpanded]       = useState<Set<string>>(new Set());
  const [subSteps,       setSubSteps]       = useState<Record<string, TraceStep[]>>({});
  // collapse state cho multi-agent group headers (default: tất cả expanded)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  const toggleGroup = (groupId: string) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId); else next.add(groupId);
      return next;
    });
  };

  const toggleExpand = (step: TraceStep) => {
    // Use span ID as key (agentConvId may be missing for single-agent path)
    const key = step.agentLfSpanId ?? step.agentConvId!;
    const next = new Set(expanded);
    if (next.has(key)) { next.delete(key); setExpanded(next); return; }
    next.add(key);
    setExpanded(next);
    if (subSteps[key] || !step.agentLfSpanId) return;

    // 1 trace duy nhất cho cả request (xem handleSendStream) → rawObservations
    // luôn chứa toàn bộ cây, không cần fetch thêm khi expand 1 [task]/single-agent.
    const descendants = getDescendants(rawObservations, step.agentLfSpanId);
    const mapped = mapObservations(descendants);
    setSubSteps(prev => ({ ...prev, [key]: mapped }));
  };

  // Reset to first row when steps change (new result)
  useEffect(() => {
    if (steps.length > 0) { setSelectedId(steps[0].id); setTab('input'); setCollapsedGroups(new Set()); }
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

  // flat list: steps + sub-steps interleaved
  const allRows: Array<{ step: TraceStep; indent: boolean }> = [];
  for (const step of steps) {
    // Inner node của multi-agent group: chỉ hiện khi group chưa bị collapse
    if (step.agentGroupId && !step.isAgentGroupHeader) {
      if (!collapsedGroups.has(step.agentGroupId)) {
        allRows.push({ step, indent: true });
      }
      continue;
    }

    allRows.push({ step, indent: false });

    // Expand inline cho [task] / single-agent spans (legacy single-trace path)
    const expandKey = step.agentLfSpanId ?? step.agentConvId;
    if (expandKey && expanded.has(expandKey)) {
      for (const sub of (subSteps[expandKey] ?? [])) {
        const subStartMs = (step.startAbsMs > 0 && sub.startAbsMs > 0)
          ? step.startMs + (sub.startAbsMs - step.startAbsMs)
          : sub.startMs + step.startMs;
        allRows.push({ step: { ...sub, startMs: subStartMs }, indent: true });
      }
    }
  }
  const headerTotalMs = Math.max(...steps.map(s => s.startMs + s.latencyMs), 1);
  const totalMs  = Math.max(...allRows.map(r => r.step.startMs + r.step.latencyMs), 1);
  // Tổng tokens/cost cho KPI header: tính trực tiếp trên rawObservations (đã
  // fetch đủ toàn bộ cây trace 1 lần, xem comment ở toggleExpand phía trên),
  // KHÔNG tính trên allRows/steps — 2 cái đó phụ thuộc UI expand state (group
  // agent nào chưa expand thì tokens/cost các generation con của nó bị 0, vì
  // span "single-agent"/"[task]" bọc ngoài không tự mang usage rollup). Tính
  // trên steps/allRows từng khiến TOKENS đổi giá trị mỗi lần bấm expand
  // (trước đó bị undercount) còn COST thì undercount vĩnh viễn vì không
  // dùng allRows. rawObservations ổn định, không đổi theo expand/collapse.
  const totalTok  = rawObservations.reduce((s, o) => s + ((o?.usage?.input ?? 0) + (o?.usage?.output ?? 0)), 0);
  const totalCost = rawObservations.reduce((s, o) => s + (o?.calculatedTotalCost ?? 0), 0);
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
          { k: 'cost',    v: '$' + totalCost.toFixed(4),         c: '#C5DD83' },
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
                    <span>{s.isConcurrentBranch ? `song song #${s.iteration}` : `retry #${s.iteration}`}</span>
                    <div style={{ flex: 1, height: 1, background: 'rgba(192,132,252,0.25)' }} />
                  </div>
                )}
                <WaterfallRow
                  step={s} index={i}
                  selected={selectedId === s.id} onSelect={() => { setSelectedId(s.id); setTab('input'); }}
                  startMs={s.startMs} totalMs={totalMs}
                  indent={indent}
                  expandable={!!s.isAgentGroupHeader || !!s.agentLfSpanId}
                  expanded={
                    s.isAgentGroupHeader
                      ? !collapsedGroups.has(s.agentGroupId!)
                      : !!(s.agentLfSpanId ?? s.agentConvId) && expanded.has(s.agentLfSpanId ?? s.agentConvId!)
                  }
                  expandLoading={false}
                  onExpand={() => {
                    if (s.isAgentGroupHeader && s.agentGroupId) toggleGroup(s.agentGroupId);
                    else if (s.agentLfSpanId) toggleExpand(s);
                  }}
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

const _CITATION_COLORS: Record<Citation['type'], string> = {
  kb: '#93C5FD', web: '#86EFAC', mcp: '#C084FC', tool: '#86EFAC', db: '#93C5FD',
};

function renderInline(text: string, citations?: Citation[]): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[\d+\])/g).map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={i} style={{ fontWeight: 600, color: '#fff' }}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85em', background: 'rgba(255,255,255,0.1)', padding: '1px 5px', borderRadius: 4, color: '#EBD07C' }}>{part.slice(1, -1)}</code>;
    const cite = part.match(/^\[(\d+)\]$/);
    if (cite && citations) {
      const c = citations.find(x => x.index === Number(cite[1]));
      if (c) {
        const color = _CITATION_COLORS[c.type] ?? '#93C5FD';
        const scoreStr = typeof c.score === 'number' ? ` · score ${c.score.toFixed(2)}` : '';
        return (
          <sup key={i} title={`${c.source}${scoreStr}\n${c.snippet}`} style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            minWidth: 15, height: 15, padding: '0 4px', marginLeft: 1,
            borderRadius: 4, fontSize: 9.5, fontWeight: 700,
            fontFamily: 'var(--font-mono)', verticalAlign: 2,
            background: `${color}29`, color,
          }}>
            {cite[1]}
          </sup>
        );
      }
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link)
      return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" style={{ color: '#93C5FD', textDecoration: 'underline' }}>{link[1]}</a>;
    return <span key={i}>{part}</span>;
  });
}

function MarkdownContent({ text, citations }: { text: string; citations?: Citation[] }) {
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
      nodes.push(<p key={`h${i}`} style={{ fontSize: sz, fontWeight: 700, color: '#fff', margin: lvl <= 2 ? '12px 0 4px' : '8px 0 2px' }}>{renderInline(hm[2], citations)}</p>);
      i++; continue;
    }
    // Unordered list
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s/, '')); i++; }
      nodes.push(
        <ul key={`ul${i}`} style={{ margin: '4px 0', paddingLeft: 18 }}>
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'rgba(255,255,255,0.78)' }}>{renderInline(item, citations)}</li>)}
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
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'rgba(255,255,255,0.78)' }}>{renderInline(item, citations)}</li>)}
        </ol>
      );
      continue;
    }
    // Blank line
    if (!line.trim()) { i++; continue; }
    // Paragraph
    nodes.push(<p key={`p${i}`} style={{ margin: '3px 0', fontSize: 13, lineHeight: 1.55 }}>{renderInline(line, citations)}</p>);
    i++;
  }
  return <>{nodes}</>;
}

/* ─── Chart renderer ─────────────────────────────────────────── */

const _CHART_COLORS = ['#EBD07C', '#93C5FD', '#86EFAC', '#F8A4A4', '#C084FC', '#7DD3FC'];

function ChartRenderer({ spec }: { spec: ChartSpec }) {
  const tooltipStyle = {
    background: '#1A1A1A',
    border: '1px solid rgba(255,255,255,0.12)',
    borderRadius: 8,
    fontSize: 11,
    color: 'rgba(255,255,255,0.85)',
  };

  return (
    <div style={{ marginTop: 8, padding: '10px 14px 4px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10 }}>
      {spec.title && (
        <p style={{ fontSize: 11.5, fontWeight: 700, color: 'rgba(255,255,255,0.7)', margin: '0 0 8px' }}>{spec.title}</p>
      )}
      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          {spec.chart_type === 'line' ? (
            <LineChart data={spec.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey={spec.x_key} stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <YAxis stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              {spec.y_keys.map((k, i) => (
                <Line key={k} type="monotone" dataKey={k} stroke={_CHART_COLORS[i % _CHART_COLORS.length]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          ) : spec.chart_type === 'area' ? (
            <AreaChart data={spec.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey={spec.x_key} stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <YAxis stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              {spec.y_keys.map((k, i) => (
                <Area key={k} type="monotone" dataKey={k} stroke={_CHART_COLORS[i % _CHART_COLORS.length]} fill={_CHART_COLORS[i % _CHART_COLORS.length]} fillOpacity={0.2} />
              ))}
            </AreaChart>
          ) : spec.chart_type === 'pie' ? (
            <PieChart>
              <Tooltip contentStyle={tooltipStyle} />
              <Pie data={spec.data} dataKey={spec.y_keys[0]} nameKey={spec.x_key} outerRadius={80}>
                {spec.data.map((_, i) => <Cell key={i} fill={_CHART_COLORS[i % _CHART_COLORS.length]} />)}
              </Pie>
            </PieChart>
          ) : spec.chart_type === 'scatter' ? (
            <ScatterChart>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
              <XAxis dataKey={spec.x_key} type="number" stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <YAxis dataKey={spec.y_keys[0]} type="number" stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3' }} />
              <Scatter data={spec.data} fill={_CHART_COLORS[0]} />
            </ScatterChart>
          ) : spec.chart_type === 'radar' ? (
            <RadarChart data={spec.data}>
              <PolarGrid stroke="rgba(255,255,255,0.12)" />
              <PolarAngleAxis dataKey={spec.x_key} stroke="rgba(255,255,255,0.4)" fontSize={10} />
              <PolarRadiusAxis stroke="rgba(255,255,255,0.2)" fontSize={9} />
              <Tooltip contentStyle={tooltipStyle} />
              {spec.y_keys.map((k, i) => (
                <Radar key={k} dataKey={k} stroke={_CHART_COLORS[i % _CHART_COLORS.length]} fill={_CHART_COLORS[i % _CHART_COLORS.length]} fillOpacity={0.25} />
              ))}
            </RadarChart>
          ) : spec.chart_type === 'radialBar' ? (
            <RadialBarChart data={spec.data} innerRadius="20%" outerRadius="90%">
              <Tooltip contentStyle={tooltipStyle} />
              <RadialBar dataKey={spec.y_keys[0]} background>
                {spec.data.map((_, i) => <Cell key={i} fill={_CHART_COLORS[i % _CHART_COLORS.length]} />)}
              </RadialBar>
              <Legend iconSize={8} wrapperStyle={{ fontSize: 10, color: 'rgba(255,255,255,0.6)' }} />
            </RadialBarChart>
          ) : spec.chart_type === 'composed' && spec.series ? (
            <ComposedChart data={spec.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey={spec.x_key} stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <YAxis stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              {spec.series.map((s, i) => {
                const color = _CHART_COLORS[i % _CHART_COLORS.length];
                if (s.type === 'line') return <Line key={s.key} type="monotone" dataKey={s.key} stroke={color} strokeWidth={2} dot={false} />;
                if (s.type === 'area') return <Area key={s.key} type="monotone" dataKey={s.key} stroke={color} fill={color} fillOpacity={0.2} />;
                return <Bar key={s.key} dataKey={s.key} fill={color} radius={[4, 4, 0, 0]} />;
              })}
            </ComposedChart>
          ) : (
            <BarChart data={spec.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" vertical={false} />
              <XAxis dataKey={spec.x_key} stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <YAxis stroke="rgba(255,255,255,0.4)" fontSize={10} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={tooltipStyle} />
              {spec.y_keys.map((k, i) => (
                <Bar key={k} dataKey={k} fill={_CHART_COLORS[i % _CHART_COLORS.length]} radius={[4, 4, 0, 0]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ─── Citation panel ─────────────────────────────────────────── */

const _CITATION_TAGS: Record<Citation['type'], string> = {
  kb: 'KB', web: 'Web', mcp: 'MCP', tool: 'Tool', db: 'DB',
};

function CitationPanel({ citations }: { citations: Citation[] }) {
  return (
    <div style={{ marginTop: 8, padding: '10px 12px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.02em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.45)', marginBottom: 8 }}>
        📎 Nguồn tham khảo
      </div>
      {citations.map((c, i) => {
        const color = _CITATION_COLORS[c.type] ?? '#93C5FD';
        const href = c.url || (c.type === 'kb' && c.doc_id ? `/api/knowledge/documents/${c.doc_id}/file` : null);
        return (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 0', borderTop: i === 0 ? 'none' : '1px solid rgba(255,255,255,0.08)' }}>
            <div style={{
              flexShrink: 0, width: 16, height: 16, borderRadius: 4, marginTop: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 9.5, fontWeight: 700, fontFamily: 'var(--font-mono)',
              background: `${color}29`, color,
            }}>
              {c.index}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, fontWeight: 600, color, textDecoration: 'none' }}>
                    {c.source}
                  </a>
                ) : (
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.85)' }}>{c.source}</span>
                )}
                <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 4, background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.45)', letterSpacing: '0.03em' }}>
                  {_CITATION_TAGS[c.type] ?? c.type}
                </span>
                {typeof c.score === 'number' && (
                  <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.25)', fontFamily: 'var(--font-mono)', marginLeft: 'auto' }}>
                    {c.score.toFixed(2)}
                  </span>
                )}
              </div>
              <div style={{
                fontSize: 11, color: 'rgba(255,255,255,0.45)', marginTop: 2, lineHeight: 1.5,
                overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
              }}>
                {c.snippet}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
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
            : <MarkdownContent text={msg.content} citations={msg.citations} />
          }
          {!isUser && msg.chartSpecs?.map((spec, i) => <ChartRenderer key={i} spec={spec} />)}
          {!isUser && msg.citations && msg.citations.length > 0 && <CitationPanel citations={msg.citations} />}
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
  const [rawObservations, setRawObservations] = useState<any[]>([]);
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
  // Khoá gửi tin nhắn trong lúc trace panel đang fetch/poll (traceLoading) —
  // gửi tiếp sẽ tạo request mới đè lên session đang chờ trace, dễ gây nhầm
  // lẫn thứ tự conversation.
  const canSend = input.trim().length > 0 && !sending && !traceLoading && (mode === 'unified' || !!selectedAgent);

  /* ── Send — unified handler cho cả single agent và multi-agent ── */
  const handleSendStream = async (text: string) => {
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
      const res = await fetch(`${API_URL}/api/orchestrate/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(keycloak?.token ? { Authorization: `Bearer ${keycloak.token}` } : {}) },
        body: JSON.stringify({
          query:    text,
          agent_id: mode === 'single' ? selectedAgent : undefined,
          user_id:  keycloak?.tokenParsed?.sub ?? 'playground',
          messages: messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
        }),
      });

      const reader  = res.body!.getReader();
      const decoder = new TextDecoder();
      let fullText   = '';
      let sessionId: string | undefined;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          if (data.startsWith('[ERROR]')) {
            const errMsg = data.slice(7).trim() || 'Đã xảy ra lỗi, vui lòng thử lại.';
            setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: errMsg } : m));
            break;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed?.__suggestions) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, suggestions: parsed.__suggestions } : m));
            } else if (parsed?.__session_id) {
              // session_id = trace_id — biết ngay từ đầu, dùng để fetch 1 trace
              // duy nhất (bao gồm cả outer node lẫn agent con) sau khi stream xong.
              sessionId = parsed.__session_id;
              setConversationId(sessionId ?? null);
            } else if (parsed?.__clarification) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: '' } : m));
            } else if (parsed?.__pending_review) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: '' } : m));
              setPendingApproval({
                convId:        parsed.__pending_review.conv_id ?? parsed.__pending_review,
                placeholderId,
                payload:       parsed.__pending_review.payload ?? null,
              });
            } else if (parsed?.__chart) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, chartSpecs: parsed.__chart } : m));
            } else if (parsed?.__citations) {
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, citations: parsed.__citations } : m));
            } else if (typeof parsed === 'string') {
              fullText += parsed;
              setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: fullText } : m));
            }
          } catch {
            fullText += data;
            setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: fullText } : m));
          }
        }
      }

      // Stream xong → fetch trace theo session_id (= trace_id). Langfuse v3 ghi
      // dữ liệu BẤT ĐỒNG BỘ (Redis queue → Clickhouse worker), nên lần fetch
      // đầu có thể chỉ thấy 1 phần observations (vd thiếu "suggestion" — node
      // chạy sau cùng, ghi lên Clickhouse trễ hơn). KHÔNG dừng poll ngay khi
      // có kết quả đầu tiên — đợi số lượng observations ỔN ĐỊNH (2 lần liên
      // tiếp bằng nhau) mới coi là đã lấy đủ.
      if (sessionId) {
        const id = sessionId;
        let prevCount = -1;
        let consecutiveErrors = 0;
        // 20 lần x 1000ms = 20s tổng — Langfuse worker (Redis→Clickhouse) đôi khi
        // trễ hơn khoảng đệm cũ (10x800ms=8s), nhất là ngay sau khi cả cụm service
        // vừa restart. 1 lỗi network thoáng qua không được làm dừng hẳn polling —
        // chỉ bỏ cuộc sau nhiều lần lỗi LIÊN TIẾP (backend thật sự có vấn đề).
        for (let i = 0; i < 20; i++) {
          try {
            const { observations, traceId: obsTraceId } = await apiGet<any>(`/api/orchestrate/traces/${id}`);
            consecutiveErrors = 0;
            const mapped = mapObservations(observations ?? [], obsTraceId);
            if (mapped.length > 0) {
              setTraceSteps(mapped);
              setRawObservations(observations ?? []);
              if (mapped.length === prevCount) break; // ổn định 2 lần liên tiếp → dừng
              prevCount = mapped.length;
            }
          } catch {
            consecutiveErrors++;
            if (consecutiveErrors >= 4) break;
          }
          await new Promise(r => setTimeout(r, 1000));
        }
      }
      setTraceLoading(false);
    } catch {
      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, content: 'Lỗi: không kết nối được backend.' } : m,
      ));
      setTraceLoading(false);
    } finally {
      setSending(false);
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
    if (!text || sending || traceLoading) return;
    setPendingOptions(null);
    if (mode === 'single' && !selectedAgent) return;
    handleSendStream(text);
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
              <button key={id} onClick={() => { setMode(id); setMessages([]); setTraceSteps([]); setRawObservations([]); }} style={{
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
                disabled={traceLoading}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                placeholder={
                  traceLoading
                    ? 'Đang tải trace, vui lòng đợi…'
                    : mode === 'unified'
                      ? 'Ask anything — orchestrator plans & assigns agents…'
                      : !selectedAgent
                        ? 'Select an agent first…'
                        : 'Ask the agent anything… (Enter to send)'
                }
                rows={1}
                style={{ flex: 1, background: 'transparent', fontSize: 13, color: 'rgba(255,255,255,0.8)', resize: 'none', outline: 'none', border: 'none', lineHeight: 1.55, fontFamily: 'inherit', maxHeight: 120, overflowY: 'auto', cursor: traceLoading ? 'not-allowed' : 'text' }}
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
              <TracePanel steps={traceSteps} traceLoading={traceLoading} traceId={conversationId} rawObservations={rawObservations} />
            </motion.div>
          )}
        </AnimatePresence>

      </div>
    </div>
  );
}