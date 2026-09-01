/**
 * "Hỏi dữ liệu" — the front door for business users.
 *
 * Three columns: scope + saved questions + history on the left, the
 * conversation in the middle, and "where did this number come from" on the
 * right. Everything shown comes from the backend frames the Playground
 * already received; the only client-side state is per-viewer convenience
 * (saved questions, history) in localStorage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  Bookmark, Bot, Brain, ChevronRight, History, Mic, MicOff, PanelRightClose, PanelRightOpen,
  Send, ShieldCheck, Square, Trash2, User,
} from 'lucide-react';
import { useAppState } from '../../AppStateContext';
import { cn } from '../../lib/utils';
import { formatRelative } from '../../lib/formatVN';
import { fetchAgents } from './api';
import { useAgentStream } from './hooks/useAgentStream';
import { AnswerBlock } from './components/AnswerBlock';
import { AnswerTheme } from './components/AnswerTheme';
import { EvidencePanel } from './components/EvidencePanel';
import type { AgentOption, AskMessage } from './types';

/* ─── per-viewer convenience storage ─────────────────────────────── */

interface StoredQuestion { q: string; at: string }
const SAVED_KEY = 'dataagent.ask.saved';
const HISTORY_KEY = 'dataagent.ask.history';
const HISTORY_MAX = 30;

function readList(key: string): StoredQuestion[] {
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(x => x && typeof x.q === 'string') : [];
  } catch { return []; }
}
function writeList(key: string, list: StoredQuestion[]) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch { /* storage unavailable */ }
}

/* ─── voice input (Web Speech API, vi-VN) ─────────────────────────── */

type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null; onerror: (() => void) | null;
  start: () => void; stop: () => void;
};
function speechRecognition(): (new () => Recognition) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const EXAMPLES = [
  'Doanh thu thuần tháng này theo vùng',
  'So sánh số đơn hàng quý này với quý trước',
  'Top 10 khách hàng theo doanh thu năm nay',
];

/* ─── page ────────────────────────────────────────────────────────── */

export default function AskDataPage() {
  const { user } = useAppState();
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [scope, setScope] = useState<string>('auto');
  const [input, setInput] = useState('');
  const [saved, setSaved] = useState<StoredQuestion[]>(() => readList(SAVED_KEY));
  const [history, setHistory] = useState<StoredQuestion[]>(() => readList(HISTORY_KEY));
  const [panelOpen, setPanelOpen] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const recogRef = useRef<Recognition | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { messages, sending, send, answerClarification, stop } = useAgentStream({
    userId: user.id,
    agentId: scope === 'auto' ? null : scope,
  });

  useEffect(() => {
    let alive = true;
    fetchAgents()
      .then(list => { if (alive) setAgents(list); })
      .catch(() => { /* backend not reachable — scope stays "auto" */ })
      .finally(() => { if (alive) setAgentsLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const lastAssistant = useMemo(() => [...messages].reverse().find(m => m.role === 'assistant') ?? null, [messages]);
  const selected: AskMessage | null = useMemo(
    () => (selectedId ? messages.find(m => m.id === selectedId) ?? lastAssistant : lastAssistant),
    [messages, selectedId, lastAssistant],
  );
  const questionFor = useCallback((assistantId: string) => {
    const idx = messages.findIndex(m => m.id === assistantId);
    for (let i = idx - 1; i >= 0; i--) if (messages[i].role === 'user') return messages[i].content;
    return '';
  }, [messages]);

  const pushHistory = (q: string) => {
    setHistory(prev => {
      const next = [{ q, at: new Date().toISOString() }, ...prev.filter(x => x.q !== q)].slice(0, HISTORY_MAX);
      writeList(HISTORY_KEY, next);
      return next;
    });
  };
  const saveQuestion = (q: string) => {
    setSaved(prev => {
      if (prev.some(x => x.q === q)) return prev;
      const next = [{ q, at: new Date().toISOString() }, ...prev];
      writeList(SAVED_KEY, next);
      return next;
    });
  };
  const removeSaved = (q: string) => {
    setSaved(prev => { const next = prev.filter(x => x.q !== q); writeList(SAVED_KEY, next); return next; });
  };

  const ask = (text?: string) => {
    const q = (text ?? input).trim();
    if (!q || sending) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSelectedId(null);
    pushHistory(q);
    void send(q);
  };

  const toggleMic = () => {
    const SR = speechRecognition();
    if (!SR) return;
    if (listening) { recogRef.current?.stop(); return; }
    const r = new SR();
    r.lang = 'vi-VN'; r.interimResults = false; r.continuous = false;
    r.onresult = e => {
      const t = Array.from({ length: e.results.length }, (_, i) => e.results[i][0]?.transcript ?? '').join(' ').trim();
      if (t) setInput(prev => (prev ? `${prev} ${t}` : t));
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recogRef.current = r;
    setListening(true);
    try { r.start(); } catch { setListening(false); }
  };
  const micAvailable = !!speechRecognition();

  const adjustHeight = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  };

  const scopeLabel = scope === 'auto' ? 'Tự động — điều phối chọn agent' : agents.find(a => a.id === scope)?.label ?? scope;

  return (
    <AnswerTheme mode="light" className="ask-workspace">
      <div className="ask-shell">
        {/* left: scope · saved · history */}
        <aside className="ask-sidebar">
          <section className="ask-side-section">
            <div className="text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2">Phạm vi</div>
            <div className="space-y-0.5">
              <button type="button" onClick={() => setScope('auto')} className={cn('subnav-item', scope === 'auto' && 'active')}>
                <Brain className="subnav-icon w-3.5 h-3.5" /><span className="flex-1 truncate">Tự động</span>
              </button>
              {agentsLoading && <div className="text-[11px] text-[#5F5F5F] px-3 py-1">Đang tải agent…</div>}
              {agents.map(a => (
                <button key={a.id} type="button" onClick={() => setScope(a.id)} className={cn('subnav-item', scope === a.id && 'active')} title={a.description}>
                  <Bot className="subnav-icon w-3.5 h-3.5" /><span className="flex-1 truncate">{a.label}</span>
                </button>
              ))}
              {!agentsLoading && agents.length === 0 && (
                <div className="text-[11px] text-[#5F5F5F] px-3 py-1 leading-relaxed">Chưa tải được danh sách agent — câu hỏi sẽ được điều phối tự động.</div>
              )}
            </div>
          </section>

          <section className="ask-side-section">
            <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2"><Bookmark size={11} /> Câu hỏi đã lưu</div>
            {saved.length === 0 ? (
              <div className="text-[11px] text-[#5F5F5F] leading-relaxed">Bấm “Lưu câu hỏi” dưới một câu trả lời để ghim lại đây.</div>
            ) : (
              <ul className="space-y-1 m-0 p-0 list-none">
                {saved.map(s => (
                  <li key={s.q} className="group flex items-start gap-1">
                    <button type="button" onClick={() => ask(s.q)} disabled={sending} className="flex-1 text-left text-[12px] text-[#2A2A2A] hover:text-[#8A5A00] leading-snug" title="Hỏi lại">
                      {s.q}
                    </button>
                    <button type="button" onClick={() => removeSaved(s.q)} aria-label="Bỏ lưu" className="opacity-0 group-hover:opacity-100 text-[#5F5F5F] hover:text-[#A83A32] shrink-0 mt-0.5"><Trash2 size={11} /></button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="ask-side-section">
            <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2"><History size={11} /> Lịch sử</div>
            {history.length === 0 ? (
              <div className="text-[11px] text-[#5F5F5F]">Chưa có câu hỏi nào.</div>
            ) : (
              <ul className="space-y-1.5 m-0 p-0 list-none">
                {history.slice(0, 12).map(h => (
                  <li key={h.q}>
                    <button type="button" onClick={() => setInput(h.q)} className="w-full text-left" title="Đưa vào ô nhập">
                      <div className="text-[12px] text-[#2A2A2A] hover:text-[#8A5A00] leading-snug truncate">{h.q}</div>
                      <div className="text-[10px] text-[#5F5F5F]">{formatRelative(h.at)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>

        {/* middle: conversation */}
        <section className="ask-chat-card">
          <div className="ask-chat-header">
            <div className="ask-chat-title">
              <div className="ask-chat-icon"><ShieldCheck size={16} /></div>
              <div className="min-w-0">
                <h2>Hỏi dữ liệu</h2>
                <p>{scopeLabel}</p>
              </div>
            </div>
            <div className="ask-chat-actions">
              <button type="button" className="ab-btn" onClick={() => setPanelOpen(v => !v)} aria-pressed={panelOpen}>
                {panelOpen ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />} {panelOpen ? 'Ẩn bằng chứng' : 'Hiện bằng chứng'}
              </button>
              {sending && (
                <button type="button" className="ab-btn" onClick={stop}><Square size={10} /> Dừng</button>
              )}
            </div>
          </div>
          <div className="ask-scope-row xl:hidden">
            <button type="button" onClick={() => setScope('auto')} className={cn('ask-scope-pill', scope === 'auto' && 'active')}>
              <Brain size={12} /> Tự động
            </button>
            {agents.map(a => (
              <button key={a.id} type="button" onClick={() => setScope(a.id)} className={cn('ask-scope-pill', scope === a.id && 'active')} title={a.description}>
                <Bot size={12} /> {a.label}
              </button>
            ))}
          </div>

          <div className="ask-scroll">
            {messages.length === 0 && (
              <div className="ask-empty">
                <div className="ask-empty-icon">
                  <ShieldCheck className="w-6 h-6" />
                </div>
                <h3>Bắt đầu bằng một câu hỏi rõ ràng</h3>
                <p>Hệ thống sẽ hỏi lại khi thiếu kỳ, chỉ số hoặc phạm vi dữ liệu trước khi chạy truy vấn.</p>
                <div className="flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map(e => (
                    <button key={e} type="button" className="ab-chip-btn" onClick={() => setInput(e)}>{e}</button>
                  ))}
                </div>
              </div>
            )}

            <AnimatePresence initial={false}>
              {messages.map(msg => (
                <motion.div key={msg.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className={cn('ask-message-row', msg.role === 'user' && 'user')}>
                  <div className={cn('ask-avatar', msg.role === 'user' ? 'user' : 'assistant')}>
                    {msg.role === 'user' ? <User size={13} /> : <Bot size={13} />}
                  </div>
                  {msg.role === 'user' ? (
                    <div className="ask-user-bubble">
                      {msg.content}
                    </div>
                  ) : (
                    <div
                      onClick={() => setSelectedId(msg.id)}
                      className={cn('ask-assistant-bubble', selected?.id === msg.id && panelOpen && 'selected')}
                    >
                      <AnswerBlock
                        msg={msg}
                        question={questionFor(msg.id)}
                        busy={sending}
                        onSuggestion={ask}
                        onAnswerClarification={answer => void answerClarification(msg.id, answer)}
                        onSave={() => { const q = questionFor(msg.id); if (q) saveQuestion(q); }}
                        onOpenEvidence={() => { setSelectedId(msg.id); setPanelOpen(true); }}
                        showEvidenceStrip={!panelOpen || selected?.id !== msg.id}
                      />
                    </div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>
            <div ref={bottomRef} />
          </div>

          <div className="ask-composer-wrap">
            <div className="ask-composer">
              <textarea
                ref={textareaRef}
                value={input}
                disabled={sending}
                rows={1}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
                placeholder={sending ? 'Đang trả lời…' : 'Hỏi về dữ liệu của bạn…'}
                aria-label="Câu hỏi"
                className="ask-input"
                style={{ maxHeight: 140 }}
              />
              {micAvailable && (
                <button
                  type="button"
                  onClick={toggleMic}
                  aria-pressed={listening}
                  aria-label={listening ? 'Dừng ghi âm' : 'Nói câu hỏi'}
                  title={listening ? 'Dừng ghi âm' : 'Nói câu hỏi (tiếng Việt)'}
                  className={cn('ask-icon-button', listening && 'listening')}
                >
                  {listening ? <MicOff size={15} /> : <Mic size={15} />}
                </button>
              )}
              <button
                type="button"
                onClick={() => ask()}
                disabled={!input.trim() || sending}
                aria-label="Gửi câu hỏi"
                className="ask-send-button"
              >
                <Send size={15} />
              </button>
            </div>
          </div>
        </section>

        {/* right: evidence */}
        <AnimatePresence initial={false}>
          {panelOpen && (
            <motion.aside
              key="evidence"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 340, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="hidden lg:flex content-card"
              style={{ flexDirection: 'column', flexShrink: 0, padding: 0, overflow: 'hidden' }}
            >
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#BFA66A] shrink-0">
                <ShieldCheck size={13} className="text-[#B88719]" />
                <span className="text-[12px] font-semibold text-[#111111]">Con số này ở đâu ra</span>
                <ChevronRight size={12} className="text-[#5F5F5F] ml-auto" />
              </div>
              <div style={{ flex: 1, overflowY: 'auto', padding: 14, width: 340 }}>
                <EvidencePanel message={selected} />
              </div>
            </motion.aside>
          )}
        </AnimatePresence>
      </div>
    </AnswerTheme>
  );
}
