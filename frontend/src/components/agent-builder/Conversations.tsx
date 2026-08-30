import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  Search, X, Download, ChevronDown, ChevronRight,
  Clock, Zap, Bot, User, MessageCircle, Send,
} from 'lucide-react';
import { cn } from '../../lib/utils';

/* ─── Types ─────────────────────────────────────────────── */
interface ToolCall {
  name: string;
  input: Record<string, string | number>;
  output: string;
}
interface ChatMsg {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  toolCalls?: ToolCall[];
  tokens?: number;
  latencyMs?: number;
}
interface Conversation {
  id: string;
  agentName: string;
  user: string;
  startedAt: string;
  duration: string;
  messageCount: number;
  status: 'active' | 'ended';
  messages: ChatMsg[];
}

/* ─── Mock data ──────────────────────────────────────────── */
const MOCK: Conversation[] = [
  {
    id: 'conv-8a2f',
    agentName: 'RAG Agent (Qwen3-4B)',
    user: 'nguyen.a@gtel.vn',
    startedAt: '2026-05-22 09:14',
    duration: '3m 42s',
    messageCount: 6,
    status: 'ended',
    messages: [
      { id: 'm1', role: 'user', content: 'What are the penalty regulations for late tender submissions?', timestamp: '09:14:02' },
      {
        id: 'm2', role: 'assistant',
        content: 'According to Article 11, Section 3 of Circular 09/2023/TT-BKHDT, contractors submitting bids after the deadline will be penalized 0.05% of the contract value per day, up to a maximum of 5%.',
        timestamp: '09:14:05',
        toolCalls: [
          { name: 'kb_search', input: { query: 'penalty regulations late tender', top_k: 5 }, output: '5 chunks retrieved (cosine sim > 0.78)' },
          { name: 'rerank', input: { candidates: 5, top_k: 3 }, output: 'Top 3 selected (scores: 0.93, 0.87, 0.81)' },
        ],
        tokens: 812, latencyMs: 1540,
      },
      { id: 'm3', role: 'user', content: 'Is there an exception for force majeure events?', timestamp: '09:15:10' },
      {
        id: 'm4', role: 'assistant',
        content: 'Yes. Article 12 provides force majeure exceptions (natural disasters, epidemics, war). Documentation must be submitted within 5 business days of the event.',
        timestamp: '09:15:14',
        toolCalls: [{ name: 'kb_search', input: { query: 'force majeure tender exception' }, output: '3 chunks retrieved' }],
        tokens: 634, latencyMs: 1210,
      },
      { id: 'm5', role: 'user', content: 'What documents are required to claim force majeure?', timestamp: '09:16:30' },
      { id: 'm6', role: 'assistant', content: 'Required: (1) Official certification from local authorities, (2) Photos/evidence of the event, (3) Timeline of impact on operations.', timestamp: '09:16:34', tokens: 410, latencyMs: 980 },
    ],
  },
  {
    id: 'conv-3c9d',
    agentName: 'Full Agent (Qwen3.6-27B)',
    user: 'tran.b@gtel.vn',
    startedAt: '2026-05-22 10:02',
    duration: '7m 18s',
    messageCount: 10,
    status: 'ended',
    messages: [
      { id: 'm1', role: 'user', content: 'Summarize the compliance requirements for Q2 2026.', timestamp: '10:02:11' },
      { id: 'm2', role: 'assistant', content: 'Q2 2026 compliance requirements: (1) SOC 2 Type II audit by June 30, (2) GDPR data mapping update, (3) Monthly pen-test reports.', timestamp: '10:02:18', tokens: 1024, latencyMs: 2100 },
      { id: 'm3', role: 'user', content: 'Which teams are responsible for the SOC 2 audit?', timestamp: '10:05:40' },
      { id: 'm4', role: 'assistant', content: 'The Security and Engineering teams are jointly responsible, coordinated by the CISO office. External auditor: Deloitte.', timestamp: '10:05:47', tokens: 380, latencyMs: 1450 },
    ],
  },
  {
    id: 'conv-7e1b',
    agentName: 'Tender Analysis Workflow',
    user: 'le.c@gtel.vn',
    startedAt: '2026-05-22 11:30',
    duration: '1m 05s',
    messageCount: 2,
    status: 'active',
    messages: [
      { id: 'm1', role: 'user', content: 'Analyze tender document TEN-2026-0045', timestamp: '11:30:00' },
      {
        id: 'm2', role: 'assistant',
        content: 'Workflow initiated. Extracting clauses and cross-referencing regulatory database.',
        timestamp: '11:30:03',
        toolCalls: [{ name: 'parse_document', input: { doc_id: 'TEN-2026-0045' }, output: 'Document parsed: 42 clauses extracted' }],
        tokens: 128, latencyMs: 420,
      },
    ],
  },
  {
    id: 'conv-5f4a',
    agentName: 'RAG Agent (Qwen3-4B)',
    user: 'pham.d@gtel.vn',
    startedAt: '2026-05-21 14:20',
    duration: '5m 50s',
    messageCount: 8,
    status: 'ended',
    messages: [
      { id: 'm1', role: 'user', content: 'What is the minimum bid bond requirement?', timestamp: '14:20:05' },
      {
        id: 'm2', role: 'assistant',
        content: 'The minimum bid bond is 1–3% of the total estimated contract value per Decree 24/2024/NĐ-CP.',
        timestamp: '14:20:09',
        toolCalls: [{ name: 'kb_search', input: { query: 'bid bond minimum requirement' }, output: '4 chunks retrieved' }],
        tokens: 520, latencyMs: 1100,
      },
    ],
  },
  {
    id: 'conv-2d8e',
    agentName: 'Compliance Check Workflow',
    user: 'hoang.e@gtel.vn',
    startedAt: '2026-05-21 16:45',
    duration: '2m 33s',
    messageCount: 4,
    status: 'ended',
    messages: [
      { id: 'm1', role: 'user', content: 'Run compliance check for vendor VND-2026-112', timestamp: '16:45:00' },
      {
        id: 'm2', role: 'assistant',
        content: 'Compliance check complete. Vendor VND-2026-112 passed 18/20 checks. 2 warnings: (1) Missing tax cert update, (2) Insurance expiry in 30 days.',
        timestamp: '16:45:08',
        toolCalls: [{ name: 'query_dashboard', input: { vendor_id: 'VND-2026-112' }, output: '{ "status": "partial", "passed": 18, "warnings": 2 }' }],
        tokens: 748, latencyMs: 1830,
      },
    ],
  },
  {
    id: 'conv-9b3c',
    agentName: 'RAG Agent (Qwen3-4B)',
    user: 'vu.f@gtel.vn',
    startedAt: '2026-05-21 08:55',
    duration: '4m 12s',
    messageCount: 5,
    status: 'ended',
    messages: [
      { id: 'm1', role: 'user', content: 'What is the contract value threshold for open bidding?', timestamp: '08:55:02' },
      { id: 'm2', role: 'assistant', content: 'Per Decree 24/2024, open bidding is required for contracts exceeding VND 200 million for goods/services and VND 1 billion for construction.', timestamp: '08:55:06', toolCalls: [{ name: 'kb_search', input: { query: 'open bidding contract value threshold' }, output: '3 chunks retrieved' }], tokens: 440, latencyMs: 1020 },
    ],
  },
];

const AGENT_OPTIONS = ['All agents', 'RAG Agent (Qwen3-4B)', 'Full Agent (Qwen3.6-27B)', 'Tender Analysis Workflow', 'Compliance Check Workflow'];

/* ─── Tool call card ─────────────────────────────────────── */
function ToolCallCard({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 border border-[#BFA66A]/40 rounded-lg overflow-hidden text-[11px]">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 bg-[#FFF9E8] hover:bg-[#F8EBC4] transition-colors text-left"
      >
        <span className="font-mono text-[#7C5A0E] font-medium">{tc.name}()</span>
        <span className="ml-auto text-[#5A5A5A]">{open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }}
            transition={{ duration: 0.15 }} className="overflow-hidden"
          >
            <div className="px-2.5 py-2 space-y-2 border-t border-[#BFA66A]/30 bg-white">
              <div>
                <p className="text-[10px] text-[#5A5A5A] mb-0.5 uppercase tracking-wider">Input</p>
                <pre className="text-[#2A2A2A] font-mono text-[10px] leading-relaxed whitespace-pre-wrap bg-[#FCFBF7] rounded p-1.5">{JSON.stringify(tc.input, null, 2)}</pre>
              </div>
              <div>
                <p className="text-[10px] text-[#5A5A5A] mb-0.5 uppercase tracking-wider">Output</p>
                <p className="text-[#2A2A2A] font-mono text-[10px] bg-[#FCFBF7] rounded p-1.5">{tc.output}</p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ─── Chat bubble ─────────────────────────────────────────── */
function MsgBubble({ msg }: { msg: ChatMsg }) {
  const isUser = msg.role === 'user';
  return (
    <div className={cn('flex gap-2.5', isUser && 'flex-row-reverse')}>
      <div className={cn('w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 border',
        isUser ? 'bg-[#F3E2A7] border-[#BFA66A]' : 'bg-[#F8F7F2] border-[#D9CDB8]')}>
        {isUser ? <User size={12} className="text-[#7C5A0E]" /> : <Bot size={12} className="text-[#5A5A5A]" />}
      </div>
      <div className={cn('max-w-[78%] flex flex-col gap-1', isUser ? 'items-end' : 'items-start')}>
        <div className={cn('rounded-xl px-3 py-2 text-xs leading-relaxed',
          isUser
            ? 'bg-[#F3E2A7] text-[#111111] rounded-tr-sm border border-[#BFA66A]/50'
            : 'bg-white text-[#2A2A2A] rounded-tl-sm border border-[#E5DED0]')}>
          {msg.content}
          {msg.toolCalls?.map((tc, i) => <ToolCallCard key={i} tc={tc} />)}
        </div>
        <div className="flex items-center gap-2 px-1">
          <span className="text-[10px] text-[#8A8A7A]">{msg.timestamp}</span>
          {msg.tokens != null && (
            <span className="text-[10px] text-[#8A8A7A] flex items-center gap-0.5">
              <Zap size={9} /> {msg.tokens}tk
            </span>
          )}
          {msg.latencyMs != null && (
            <span className="text-[10px] text-[#8A8A7A] flex items-center gap-0.5">
              <Clock size={9} /> {msg.latencyMs}ms
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Drawer ──────────────────────────────────────────────── */
function ConvDrawer({ conv, onClose }: { conv: Conversation; onClose: () => void }) {
  const [messages, setMessages] = useState<ChatMsg[]>(conv.messages);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || sending) return;
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    setMessages(prev => [...prev, { id: Date.now().toString(), role: 'user', content: text, timestamp: now }]);
    setInput('');
    setSending(true);
    setTimeout(() => {
      setMessages(prev => [
        ...prev,
        { id: (Date.now() + 1).toString(), role: 'assistant', content: 'This is a mock response. Connect to the backend to continue the conversation with real AI responses.', timestamp: new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }), tokens: 48, latencyMs: 640 },
      ]);
      setSending(false);
    }, 1000);
  };

  return (
    <motion.div
      initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
      transition={{ type: 'spring', damping: 28, stiffness: 260 }}
      className="fixed top-0 right-0 h-full w-[500px] bg-[#FCFBF7] border-l border-[#BFA66A] flex flex-col z-50 shadow-2xl"
    >
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#BFA66A]/50 shrink-0 bg-white">
        <div>
          <p className="text-sm font-semibold text-[#111111] font-mono">{conv.id}</p>
          <p className="text-[11px] text-[#5A5A5A] mt-0.5">{conv.agentName} · {conv.user}</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-[11px] py-1.5 px-3 flex items-center gap-1.5">
            <Download size={12} /> Export
          </button>
          <button onClick={onClose} className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-[#F3E2A7] text-[#5A5A5A] hover:text-[#111111] transition-colors border border-transparent hover:border-[#BFA66A]">
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="px-2 py-1.5 border-b border-[#BFA66A]/30 bg-[#FFF9E8] shrink-0">
        <div className="flex items-center gap-4 px-3 text-[11px] text-[#5A5A5A]">
          <span>Started: {conv.startedAt}</span>
          <span>Duration: {conv.duration}</span>
          <span>{messages.length} messages</span>
          <span className={cn('ml-auto inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold',
            conv.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500')}>
            {conv.status === 'active' && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}
            {conv.status}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-5 space-y-4">
        {messages.map(msg => <MsgBubble key={msg.id} msg={msg} />)}
        {sending && (
          <div className="flex gap-2.5">
            <div className="w-7 h-7 rounded-full bg-[#F8F7F2] border border-[#D9CDB8] flex items-center justify-center shrink-0">
              <Bot size={12} className="text-[#5A5A5A]" />
            </div>
            <div className="bg-white border border-[#E5DED0] rounded-xl rounded-tl-sm px-3 py-2.5 flex items-center gap-1.5">
              {[0, 1, 2].map(i => (
                <span key={i} className="w-1.5 h-1.5 rounded-full bg-[#BFA66A] animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
              ))}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Continue chat input */}
      <div className="px-4 pb-4 pt-2 border-t border-[#BFA66A]/30 shrink-0 bg-white">
        <div className="flex gap-2 items-end bg-[#FCFBF7] border border-[#BFA66A]/60 rounded-xl px-3 py-2 focus-within:border-[#8A5A00] transition-colors">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder="Continue this conversation… (Enter to send)"
            rows={1}
            className="flex-1 bg-transparent text-sm text-[#111111] placeholder-[#8A8A7A] resize-none focus:outline-none leading-relaxed"
            style={{ maxHeight: 100 }}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending}
            className="shrink-0 w-8 h-8 rounded-lg bg-[#111111] hover:bg-[#333] disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
          >
            <Send size={13} className="text-white" />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

/* ─── Main ────────────────────────────────────────────────── */
export default function Conversations() {
  const [search, setSearch] = useState('');
  const [agentFilter, setAgentFilter] = useState('All agents');
  const [statusFilter, setStatusFilter] = useState('all');
  const [activeConv, setActiveConv] = useState<Conversation | null>(null);

  const totalConvs = MOCK.length;
  const activeUsers = new Set(MOCK.map(c => c.user)).size;
  const avgMessages = Math.round(MOCK.reduce((s, c) => s + c.messageCount, 0) / MOCK.length);

  const filtered = MOCK.filter(c => {
    const matchSearch = c.id.includes(search) || c.user.includes(search.toLowerCase()) || c.agentName.toLowerCase().includes(search.toLowerCase());
    const matchAgent  = agentFilter === 'All agents' || c.agentName === agentFilter;
    const matchStatus = statusFilter === 'all' || c.status === statusFilter;
    return matchSearch && matchAgent && matchStatus;
  });

  return (
    <div className="space-y-5">
      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Total Conversations', value: totalConvs,   icon: MessageCircle },
          { label: 'Active Users',         value: activeUsers,  icon: User },
          { label: 'Avg Messages / Session', value: avgMessages, icon: Zap },
        ].map(s => (
          <div key={s.label} className="warm-card rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <s.icon size={14} className="text-[#B88719]" />
              <p className="text-[11px] text-[#5A5A5A] uppercase tracking-wider font-medium">{s.label}</p>
            </div>
            <p className="text-3xl font-bold font-display text-[#111111]">{s.value}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#8A8A7A]" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search ID, user, agent…"
            className="pl-9 pr-3 py-2 bg-white border border-[#BFA66A]/60 rounded-lg text-xs text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#8A5A00] w-60"
          />
        </div>
        <select
          value={agentFilter} onChange={e => setAgentFilter(e.target.value)}
          className="bg-white border border-[#BFA66A]/60 text-xs text-[#2A2A2A] rounded-lg px-3 py-2 focus:outline-none focus:border-[#8A5A00] cursor-pointer"
        >
          {AGENT_OPTIONS.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <select
          value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
          className="bg-white border border-[#BFA66A]/60 text-xs text-[#2A2A2A] rounded-lg px-3 py-2 focus:outline-none focus:border-[#8A5A00] cursor-pointer"
        >
          <option value="all">All status</option>
          <option value="active">Active</option>
          <option value="ended">Ended</option>
        </select>
        <span className="text-xs text-[#5A5A5A] ml-1">{filtered.length} conversations</span>
      </div>

      {/* Table */}
      <div className="warm-panel rounded-xl overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#BFA66A]/40 bg-[#FFF9E8]">
              {['Conversation ID', 'Agent', 'User', 'Started At', 'Duration', 'Msgs', 'Status', ''].map(h => (
                <th key={h} className="text-left px-4 py-3 text-[#5A5A5A] font-semibold uppercase tracking-wider text-[10px]">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((conv, i) => (
              <tr
                key={conv.id}
                className={cn('border-b border-[#BFA66A]/20 hover:bg-[#FFF9E8] cursor-pointer transition-colors', i === filtered.length - 1 && 'border-b-0')}
                onClick={() => setActiveConv(conv)}
              >
                <td className="px-4 py-3 font-mono text-[11px] text-[#7C5A0E] font-medium">{conv.id}</td>
                <td className="px-4 py-3 text-[#2A2A2A]">{conv.agentName}</td>
                <td className="px-4 py-3 text-[#5A5A5A]">{conv.user}</td>
                <td className="px-4 py-3 text-[#5A5A5A]">{conv.startedAt}</td>
                <td className="px-4 py-3 text-[#5A5A5A]">{conv.duration}</td>
                <td className="px-4 py-3 text-[#2A2A2A] text-center font-medium">{conv.messageCount}</td>
                <td className="px-4 py-3">
                  <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold',
                    conv.status === 'active' ? 'bg-emerald-100 text-emerald-700 border border-emerald-200' : 'bg-gray-100 text-gray-500 border border-gray-200')}>
                    {conv.status === 'active' && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />}
                    {conv.status}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <button
                    className="text-[11px] text-[#8A5A00] hover:underline font-semibold"
                    onClick={e => { e.stopPropagation(); setActiveConv(conv); }}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Drawer overlay */}
      <AnimatePresence>
        {activeConv && (
          <>
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/30 z-40"
              onClick={() => setActiveConv(null)}
            />
            <ConvDrawer conv={activeConv} onClose={() => setActiveConv(null)} />
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
