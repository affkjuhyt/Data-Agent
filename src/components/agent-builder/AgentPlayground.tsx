import React, { useState, useRef, useEffect } from 'react';
import {
  MessageSquare, Send, Bug, ChevronDown,
  Clock, Zap, Bot, User, Layers, Search,
  Brain, FileOutput, AlertCircle, CheckCircle2,
  Route, Sparkles, Copy,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8001';
const FLOW_BUILDER_URL = (import.meta as any).env?.VITE_FLOW_BUILDER_URL ?? 'http://localhost:8002';

/* ─── Types ─────────────────────────────────────────────────── */

interface TraceStep {
  id: string;
  node: string;
  status: 'success' | 'error' | 'running';
  latencyMs: number;
  tokens: number;
  input: string;
  output: string;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  suggestions?: string[];
  routed?: { name: string; conf: number } | null;
}

interface AgentOption {
  id: string;
  label: string;
  description: string;
  routing_keywords?: string[];
}

/* ─── Span palette (gold, Langfuse-style) ────────────────────── */

function spanType(node: string): { label: string; color: string; bg: string } {
  if (node === 'router')
    return { label: 'ROUTING',    color: '#F0B429', bg: 'rgba(240,180,41,0.18)' };
  if (node.includes('planner') || node.includes('responder'))
    return { label: 'GENERATION', color: '#EBD07C', bg: 'rgba(235,208,124,0.14)' };
  if (node.includes('kb_search') || node.includes('rrf') || node.includes('reranker'))
    return { label: 'RETRIEVAL',  color: '#F3E2A7', bg: 'rgba(243,226,167,0.10)' };
  if (node.includes('guardrail'))
    return { label: 'EVENT',      color: '#D9B86C', bg: 'rgba(217,184,108,0.14)' };
  return   { label: 'SPAN',       color: '#BFA66A', bg: 'rgba(191,166,106,0.12)' };
}

/* ─── Existing helpers (keep as-is) ─────────────────────────── */

const _SKIP_NODES = new Set(['LangGraph', '_route_after_guardrail_input', 'route_after_planner', 'join_retrieval_node']);

function nodeIcon(name: string): React.ReactNode {
  if (name.includes('guardrail'))  return <AlertCircle size={13} />;
  if (name.includes('planner'))    return <Brain size={13} />;
  if (name.includes('kb_search'))  return <Search size={13} />;
  if (name.includes('reranker') || name.includes('rrf')) return <Layers size={13} />;
  if (name.includes('responder'))  return <FileOutput size={13} />;
  return <Zap size={13} />;
}

function trimInput(name: string, raw: any): string {
  if (!raw || typeof raw !== 'object') return String(raw ?? '');
  const q = raw.query ?? '';
  const cfg = raw.config ?? {};
  switch (true) {
    case name.includes('guardrail'): return JSON.stringify({ query: q }, null, 2);
    case name.includes('planner'):   return JSON.stringify({ query: q, memory_context: (raw.memory_context ?? []).length + ' items' }, null, 2);
    case name.includes('kb_search'): return JSON.stringify({ query: q, mode: cfg.kb_mode, top_k: cfg.top_k }, null, 2);
    case name.includes('rrf'):       return JSON.stringify({ kb_chunks: (raw.kb_chunks ?? []).length + ' chunks', mcp_results: (raw.mcp_results ?? []).length + ' results' }, null, 2);
    case name.includes('reranker'):  return JSON.stringify({ rrf_results: (raw.rrf_results ?? []).length + ' items', top_n: cfg.reranker_top_n }, null, 2);
    case name.includes('responder'): return JSON.stringify({ query: q, reranked_chunks: (raw.reranked_chunks ?? []).length + ' chunks' }, null, 2);
    default: return JSON.stringify({ query: q }, null, 2);
  }
}

function mapObservations(obs: any[]): TraceStep[] {
  return obs
    .filter(o => !_SKIP_NODES.has(o.name) && o.type === 'SPAN')
    .sort((a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime())
    .map(o => ({
      id: o.id,
      node: o.name,
      status: o.level === 'ERROR' ? 'error' : 'success',
      latencyMs: o.endTime ? Math.round(new Date(o.endTime).getTime() - new Date(o.startTime).getTime()) : 0,
      tokens: o.usage ? ((o.usage.input ?? 0) + (o.usage.output ?? 0)) : 0,
      input: trimInput(o.name, o.input),
      output: o.level === 'ERROR' && o.statusMessage
        ? o.statusMessage
        : typeof o.output === 'object' ? JSON.stringify(o.output, null, 2) : String(o.output ?? ''),
    }));
}

/* ─── Unified chat client-side routing ──────────────────────── */

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

function WaterfallRow({ step, index, selected, onSelect, startMs, totalMs }: {
  step: TraceStep; index: number; selected: boolean;
  onSelect: (i: number) => void; startMs: number; totalMs: number;
}) {
  const t = spanType(step.node);
  const leftPct  = (startMs / Math.max(totalMs, 1)) * 100;
  const widthPct = Math.max(0.6, (step.latencyMs / Math.max(totalMs, 1)) * 100);
  const isErr = step.status === 'error';
  return (
    <button
      onClick={() => onSelect(index)}
      style={{
        width: '100%', display: 'grid',
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
        <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.88)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {step.node}
        </span>
      </div>
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
      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.45)', textAlign: 'right' }}>
        {step.latencyMs}ms
      </span>
    </button>
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
  const [tab, setTab] = useState<'input' | 'output' | 'metadata'>('input');
  const [selected, setSelected] = useState(0);
  useEffect(() => { if (selected >= steps.length && steps.length > 0) setSelected(0); }, [steps, selected]);

  let cum = 0;
  const starts = steps.map(s => { const v = cum; cum += s.latencyMs; return v; });
  const totalMs  = Math.max(cum, 1);
  const totalTok = steps.reduce((s, t) => s + t.tokens, 0);
  const sel = steps[selected];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: '#0B0B0E', minWidth: 0 }}>
      {/* Trace header */}
      <div style={{ padding: '10px 14px 8px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
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

      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {[
          { k: 'latency', v: (totalMs / 1000).toFixed(2) + 's', c: '#F5F5F5' },
          { k: 'tokens',  v: totalTok.toLocaleString(),          c: '#EBD07C' },
          { k: 'cost',    v: '$' + (totalTok * 0.0000045).toFixed(4), c: '#C5DD83' },
          { k: 'spans',   v: String(steps.length),               c: '#F3E2A7' },
        ].map(({ k, v, c }, i) => (
          <div key={k} style={{ padding: '7px 10px', borderRight: i < 3 ? '1px solid rgba(255,255,255,0.04)' : 'none' }}>
            <div style={{ fontSize: 8.5, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'rgba(255,255,255,0.3)', marginBottom: 2 }}>{k}</div>
            <div style={{ fontSize: 12, fontWeight: 600, color: c, fontFamily: 'var(--font-mono)' }}>{v}</div>
          </div>
        ))}
      </div>

      {/* Observations label */}
      <div style={{ padding: '8px 12px 4px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.1em', color: 'rgba(255,255,255,0.38)', fontWeight: 600 }}>
          Observations
        </span>
        <span style={{ fontSize: 9.5, color: 'rgba(255,255,255,0.28)', fontFamily: 'var(--font-mono)' }}>
          {traceLoading ? 'streaming…' : steps.length + ' spans · ' + totalMs + 'ms'}
        </span>
      </div>

      {/* Waterfall */}
      <div style={{ maxHeight: 210, overflowY: 'auto', padding: '2px 0 6px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {traceLoading ? (
          <div style={{ padding: '4px 12px', display: 'flex', flexDirection: 'column', gap: 5 }}>
            {[0, 1, 2, 3].map(i => (
              <div key={i} className="pulse-soft" style={{ height: 22, borderRadius: 4, background: 'rgba(255,255,255,0.04)', opacity: 1 - i * 0.18 }} />
            ))}
          </div>
        ) : steps.length === 0 ? (
          <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', textAlign: 'center', padding: '20px 12px' }}>
            Send a message to capture a trace
          </p>
        ) : steps.map((s, i) => (
          <WaterfallRow
            key={s.id}
            step={s} index={i}
            selected={selected === i}
            onSelect={setSelected}
            startMs={starts[i]}
            totalMs={totalMs}
          />
        ))}
      </div>

      {/* Selected span detail */}
      {!traceLoading && sel && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ padding: '7px 14px 5px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {(() => { const t = spanType(sel.node); return <span style={{ fontSize: 8, fontWeight: 700, padding: '2px 5px', borderRadius: 3, background: t.bg, color: t.color }}>{t.label}</span>; })()}
            <span style={{ fontSize: 11.5, fontFamily: 'var(--font-mono)', color: 'rgba(255,255,255,0.88)', fontWeight: 500 }}>{sel.node}</span>
            <span style={{ marginLeft: 'auto', fontSize: 9.5, color: 'rgba(255,255,255,0.32)', fontFamily: 'var(--font-mono)' }}>
              +{starts[selected]}ms → +{starts[selected] + sel.latencyMs}ms
            </span>
          </div>
          <div style={{ padding: '0 14px 5px', display: 'flex', alignItems: 'center', gap: 12, fontSize: 10, color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Clock size={9} />{sel.latencyMs}ms</span>
            {sel.tokens > 0 && <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}><Zap size={9} />{sel.tokens} tok</span>}
            <span style={{ color: sel.status === 'success' ? '#C5DD83' : '#F8A4A4' }}>● {sel.status}</span>
          </div>
          <div style={{ display: 'flex', padding: '0 14px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
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
          <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
            {tab === 'input'    && <JsonView value={sel.input} />}
            {tab === 'output'   && <JsonView value={sel.output} />}
            {tab === 'metadata' && <JsonView value={{ node: sel.node, type: spanType(sel.node).label.toLowerCase(), status: sel.status, latency_ms: sel.latencyMs, tokens: sel.tokens }} />}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── ChatBubble ─────────────────────────────────────────────── */

function ChatBubble({ msg }: {
  msg: Message;
}) {
  const isUser = msg.role === 'user';
  const renderContent = (text: string) =>
    text.split(/(\*\*[^*]+\*\*)/g).map((p, i) =>
      p.startsWith('**') && p.endsWith('**')
        ? <strong key={i} style={{ fontWeight: 600, color: '#fff' }}>{p.slice(2, -2)}</strong>
        : <span key={i}>{p}</span>
    );
  return (
    <div style={{ display: 'flex', gap: 10, flexDirection: isUser ? 'row-reverse' : 'row' }}>
      <div style={{
        width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: isUser ? 'rgba(235,208,124,0.22)' : 'rgba(255,255,255,0.1)',
      }}>
        {isUser
          ? <User size={13} style={{ color: '#EBD07C' }} />
          : <Bot  size={13} style={{ color: 'rgba(255,255,255,0.6)' }} />}
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
          {msg.content.split('\n').map((line, i) => (
            <p key={i} style={{ margin: i > 0 ? '4px 0 0' : 0 }}>{renderContent(line)}</p>
          ))}
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
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectorOpen, setSelectorOpen]   = useState(false);
  const [messages, setMessages]   = useState<Message[]>([]);
  const [input, setInput]         = useState('');
  const [sending, setSending]     = useState(false);
  const [traceSteps, setTraceSteps]   = useState<TraceStep[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [pendingOptions, setPendingOptions] = useState<string[] | null>(null);

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

  /* Load agents from catalog */
  useEffect(() => {
    (async () => {
      try {
        const res  = await fetch(`${API_BASE}/api/catalog/agents`, {
          headers: { 'X-Tenant-ID': import.meta.env.VITE_TENANT_ID ?? 'tenant-dev' },
        });
        const data: { id: string; name: string; description: string; routing_keywords?: string[] }[] = await res.json();
        const opts: AgentOption[] = data.map(a => ({
          id: a.id, label: a.name, description: a.description,
          routing_keywords: a.routing_keywords,
        }));
        setAgents(opts);
        if (opts.length > 0) setSelectedAgent(opts[0].id);
      } catch {
        /* backend not running */
      } finally {
        setAgentsLoading(false);
      }
    })();
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  /* ── Send (Single Agent mode — real SSE backend) ── */
  const handleSendSingle = async (text: string) => {
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: text, timestamp: now };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSending(true);

    const placeholderId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: placeholderId, role: 'assistant', content: '', timestamp: now }]);

    try {
      const res = await fetch(`${API_BASE}/api/conversations/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

      /* Poll trace */
      if (convId) {
        setTraceLoading(true);
        (async () => {
          for (let i = 0; i < 5; i++) {
            await new Promise(r => setTimeout(r, 2000));
            try {
              const tr  = await fetch(`${API_BASE}/api/traces/${convId}`);
              const { observations } = await tr.json();
              const steps = mapObservations(observations ?? []);
              if (steps.length > 0) { setTraceSteps(steps); break; }
            } catch { break; }
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

  /* ── Send (Unified Chat mode — mock routing) ── */
  const handleSendUnified = (text: string) => {
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'user', content: text, timestamp: now }]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSending(true);
    setTraceLoading(true);

    const decision = routeMessage(text, agents);

    setTimeout(() => {
      const reply: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: decision.conf > 0.5
          ? `Routed to **${decision.name}**. Based on its knowledge base I synthesized an answer with high confidence.`
          : "I'm not fully sure which specialist fits best here. Could you add a bit more detail?",
        timestamp: now,
        routed: decision,
        suggestions: ['Can you show me an example?', 'What are the next steps?', 'Summarize briefly'],
      };
      setMessages(prev => [...prev, reply]);

      /* Prepend router span */
      const routerSpan: TraceStep = {
        id: 'router-' + Date.now(), node: 'router', status: 'success',
        latencyMs: 90 + Math.round(Math.random() * 40),
        tokens: 64,
        input:  JSON.stringify({ message: text, strategy: 'HYBRID' }, null, 2),
        output: JSON.stringify({ routed_agent: decision.name, confidence: +decision.conf.toFixed(2) }, null, 2),
      };
      const mockSpans: TraceStep[] = [
        routerSpan,
        { id: 'p-' + Date.now(), node: 'planner', status: 'success', latencyMs: 160 + Math.round(Math.random() * 80), tokens: 320, input: JSON.stringify({ query: text }, null, 2), output: '{"plan": "retrieve relevant docs, synthesize response"}' },
        { id: 'kb-' + Date.now(), node: 'kb_search', status: 'success', latencyMs: 200 + Math.round(Math.random() * 120), tokens: 0, input: JSON.stringify({ query: text, top_k: 5 }, null, 2), output: '{"chunks": 4}' },
        { id: 'r-' + Date.now(), node: 'responder', status: 'success', latencyMs: 380 + Math.round(Math.random() * 200), tokens: 640 + Math.round(Math.random() * 200), input: JSON.stringify({ query: text, chunks: 4 }, null, 2), output: reply.content },
      ];
      setTraceSteps(mockSpans);
      setSending(false);
      setTraceLoading(false);
    }, 1200);
  };

  const handleSend = (override?: string) => {
    const text = (override ?? input).trim();
    if (!text || sending) return;
    if (mode === 'unified') { handleSendUnified(text); return; }
    if (!selectedAgent) return;
    handleSendSingle(text);
  };

  const agentLabel = agents.find(a => a.id === selectedAgent)?.label ?? 'Agent';

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
          {/* Mode toggle */}
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
          {/* Agent selector — Single Agent only */}
          {mode === 'single' && (
            <div ref={selectorRef} style={{ position: 'relative' }}>
              <button
                onClick={() => setSelectorOpen(v => !v)}
                disabled={agentsLoading}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                  color: 'rgba(255,255,255,0.7)', fontSize: 11.5, borderRadius: 9,
                  padding: '6px 10px', minWidth: 196, cursor: 'pointer',
                }}
              >
                <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {agentsLoading ? 'Loading…' : (agentLabel)}
                </span>
                <ChevronDown size={12} style={{ color: 'rgba(255,255,255,0.4)', transform: selectorOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
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
                        onClick={() => { setSelectedAgent(a.id); setSelectorOpen(false); setConversationId(null); setMessages([]); }}
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
          {/* Unified Chat indicator */}
          {mode === 'unified' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: 'rgba(240,180,41,0.12)', border: '1px solid rgba(240,180,41,0.4)', borderRadius: 9, padding: '6px 11px' }}>
              <Route size={13} style={{ color: '#F0B429' }} />
              <span style={{ fontSize: 11, fontWeight: 600, color: '#F0B429' }}>Router picks agent</span>
            </div>
          )}
          {/* Debug toggle */}
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
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Chat panel */}
        <div style={{ flex: debugOn ? '0 0 55%' : '1', display: 'flex', flexDirection: 'column', borderRight: debugOn ? '1px solid rgba(255,255,255,0.1)' : 'none', minWidth: 0 }}>
          {/* Context badge */}
          <div style={{ padding: '7px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)', flexShrink: 0 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>
              {mode === 'unified'
                ? <><Route size={11} />Unified chat — router selects an agent per message</>
                : <><Bot size={11} />{agentLabel}</>}
            </span>
          </div>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '14px 14px', display: 'flex', flexDirection: 'column', gap: 14 }}>
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

          {/* Suggestion chips — dynamic from backend, above input */}
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
                  <button onClick={() => setPendingOptions(null)}
                    style={{ width: '100%', textAlign: 'left', padding: '8px 10px', borderRadius: 8, border: 'none', background: 'transparent', color: 'rgba(255,255,255,0.3)', fontSize: 13, cursor: 'pointer', fontStyle: 'italic' }}>
                    Tự nhập câu trả lời...
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Input */}
          <div style={{ padding: '8px 12px 14px', flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, padding: '8px 10px' }}>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                placeholder={mode === 'unified' ? 'Ask anything — router picks the right agent…' : 'Ask the agent anything… (Enter to send)'}
                rows={1}
                style={{ flex: 1, background: 'transparent', fontSize: 13, color: 'rgba(255,255,255,0.8)', resize: 'none', outline: 'none', border: 'none', lineHeight: 1.55, fontFamily: 'inherit', maxHeight: 120, overflowY: 'auto' }}
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || sending || (mode === 'single' && !selectedAgent)}
                style={{
                  flexShrink: 0, width: 32, height: 32, borderRadius: 9,
                  background: (input.trim() && !sending) ? '#B88719' : 'rgba(184,135,25,0.25)',
                  border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: (input.trim() && !sending) ? 'pointer' : 'not-allowed', color: '#fff',
                }}
              >
                <Send size={13} />
              </button>
            </div>
          </div>
        </div>

        {/* Trace panel */}
        <AnimatePresence initial={false}>
          {debugOn && (
            <motion.div
              key="trace"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: '45%', opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: 'easeInOut' }}
              style={{ overflow: 'hidden', display: 'flex' }}
            >
              <TracePanel steps={traceSteps} traceLoading={traceLoading} traceId={conversationId} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
