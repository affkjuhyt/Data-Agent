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
    <AnswerTheme mode="light" style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 56px - 4rem)', minHeight: 560 }}>
      {/* header */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <div>
          <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] text-[#B88719] font-mono mb-1">
            <ShieldCheck size={13} /> Hỏi dữ liệu
          </div>
          <h2 className="font-display text-xl font-bold text-[#111111] m-0">Hỏi bằng tiếng Việt, nhận con số kèm bằng chứng</h2>
          <p className="text-[12.5px] text-[#5F5F5F] mt-1 m-0">Mỗi con số truy được về chỉ số đã duyệt, câu SQL đã chạy, số dòng và độ tươi của dữ liệu.</p>
        </div>
        <button type="button" className="ab-btn" onClick={() => setPanelOpen(v => !v)} aria-pressed={panelOpen}>
          {panelOpen ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />} {panelOpen ? 'Ẩn bằng chứng' : 'Hiện bằng chứng'}
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 12 }}>
        {/* left: scope · saved · history */}
        <aside className="hidden xl:flex" style={{ width: 236, flexShrink: 0, flexDirection: 'column', gap: 12, overflowY: 'auto' }}>
          <section className="content-card" style={{ padding: 12 }}>
            <div className="text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2">Phạm vi</div>
            <div className="space-y-0.5">
              <button type="button" onClick={() => setScope('auto')} className={cn('subnav-item', scope === 'auto' && 'active')}>
                <Brain className="subnav-icon w-3.5 h-3.5" /><span className="flex-1 truncate">Tự động</span>
              </button>
              {agentsLoading && <div className="text-[11px] text-[#777777] px-3 py-1">Đang tải agent…</div>}
              {agents.map(a => (
                <button key={a.id} type="button" onClick={() => setScope(a.id)} className={cn('subnav-item', scope === a.id && 'active')} title={a.description}>
                  <Bot className="subnav-icon w-3.5 h-3.5" /><span className="flex-1 truncate">{a.label}</span>
                </button>
              ))}
              {!agentsLoading && agents.length === 0 && (
                <div className="text-[11px] text-[#777777] px-3 py-1 leading-relaxed">Chưa tải được danh sách agent — câu hỏi sẽ được điều phối tự động.</div>
              )}
            </div>
          </section>

          <section className="content-card" style={{ padding: 12 }}>
            <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2"><Bookmark size={11} /> Câu hỏi đã lưu</div>
            {saved.length === 0 ? (
              <div className="text-[11px] text-[#777777] leading-relaxed">Bấm “Lưu câu hỏi” dưới một câu trả lời để ghim lại đây.</div>
            ) : (
              <ul className="space-y-1 m-0 p-0 list-none">
                {saved.map(s => (
                  <li key={s.q} className="group flex items-start gap-1">
                    <button type="button" onClick={() => ask(s.q)} disabled={sending} className="flex-1 text-left text-[12px] text-[#2A2A2A] hover:text-[#8A5A00] leading-snug" title="Hỏi lại">
                      {s.q}
                    </button>
                    <button type="button" onClick={() => removeSaved(s.q)} aria-label="Bỏ lưu" className="opacity-0 group-hover:opacity-100 text-[#777777] hover:text-[#A83A32] shrink-0 mt-0.5"><Trash2 size={11} /></button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="content-card" style={{ padding: 12 }}>
            <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F] mb-2"><History size={11} /> Lịch sử</div>
            {history.length === 0 ? (
              <div className="text-[11px] text-[#777777]">Chưa có câu hỏi nào.</div>
            ) : (
              <ul className="space-y-1.5 m-0 p-0 list-none">
                {history.slice(0, 12).map(h => (
                  <li key={h.q}>
                    <button type="button" onClick={() => setInput(h.q)} className="w-full text-left" title="Đưa vào ô nhập">
                      <div className="text-[12px] text-[#2A2A2A] hover:text-[#8A5A00] leading-snug truncate">{h.q}</div>
                      <div className="text-[10px] text-[#777777]">{formatRelative(h.at)}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>

        {/* middle: conversation */}
        <section className="content-card" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden' }}>
          <div className="flex items-center gap-2 px-4 py-2 border-b border-[#D6C79F] text-[11px] text-[#5F5F5F] shrink-0">
            {scope === 'auto' ? <Brain size={12} className="text-[#B88719]" /> : <Bot size={12} className="text-[#B88719]" />}
            <span className="truncate">{scopeLabel}</span>
            {sending && (
              <button type="button" className="ab-btn ml-auto" onClick={stop}><Square size={10} /> Dừng</button>
            )}
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {messages.length === 0 && (
              <div style={{ margin: 'auto', maxWidth: 520, textAlign: 'center' }}>
                <div className="w-12 h-12 rounded-xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center mx-auto mb-3">
                  <ShieldCheck className="w-6 h-6 text-[#B88719]" />
                </div>
                <h3 className="font-display text-[16px] font-bold text-[#111111] m-0">Hỏi về dữ liệu của bạn</h3>
                <p className="text-[12.5px] text-[#5F5F5F] mt-1 mb-4 leading-relaxed">
                  Nếu câu hỏi thiếu kỳ hoặc chỉ số nhập nhằng, hệ thống sẽ hỏi lại trước khi chạy — không đoán, không bịa số.
                </p>
                <div className="flex flex-wrap justify-center gap-2">
                  {EXAMPLES.map(e => (
                    <button key={e} type="button" className="ab-chip-btn" onClick={() => setInput(e)}>{e}</button>
                  ))}
                </div>
              </div>
            )}

            <AnimatePresence initial={false}>
              {messages.map(msg => (
                <motion.div key={msg.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} style={{ display: 'flex', gap: 10, flexDirection: msg.role === 'user' ? 'row-reverse' : 'row' }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: '50%', flexShrink: 0, marginTop: 2,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: msg.role === 'user' ? '#F3E2A7' : '#111111',
                  }}>
                    {msg.role === 'user' ? <User size={13} style={{ color: '#8A5A00' }} /> : <Bot size={13} style={{ color: '#D9B86C' }} />}
                  </div>
                  {msg.role === 'user' ? (
                    <div style={{ maxWidth: '72%', borderRadius: 12, borderTopRightRadius: 4, padding: '9px 13px', fontSize: 13, lineHeight: 1.5, background: '#FFF9E8', border: '1px solid #EBD07C', color: '#111111' }}>
                      {msg.content}
                    </div>
                  ) : (
                    <div
                      onClick={() => setSelectedId(msg.id)}
                      style={{
                        flex: 1, minWidth: 0, borderRadius: 12, borderTopLeftRadius: 4, padding: '10px 14px',
                        background: '#FFFFFF', border: `1px solid ${selected?.id === msg.id && panelOpen ? '#B88719' : '#D6C79F'}`,
                        boxShadow: selected?.id === msg.id && panelOpen ? '0 0 0 2px rgba(184,135,25,0.15)' : undefined,
                        cursor: 'default',
                      }}
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

          <div className="shrink-0 px-3 pb-3 pt-2 border-t border-[#D6C79F] bg-[#FCFBF7]">
            <div className="flex items-end gap-2 bg-white border border-[#BFA66A] rounded-xl px-3 py-2 focus-within:border-[#8A5A00]">
              <textarea
                ref={textareaRef}
                value={input}
                disabled={sending}
                rows={1}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
                placeholder={sending ? 'Đang trả lời…' : 'Hỏi về dữ liệu của bạn… (Enter để gửi, Shift+Enter xuống dòng)'}
                aria-label="Câu hỏi"
                className="flex-1 bg-transparent text-[13px] text-[#111111] placeholder:text-[#777777] outline-none resize-none leading-relaxed"
                style={{ maxHeight: 140 }}
              />
              {micAvailable && (
                <button
                  type="button"
                  onClick={toggleMic}
                  aria-pressed={listening}
                  aria-label={listening ? 'Dừng ghi âm' : 'Nói câu hỏi'}
                  title={listening ? 'Dừng ghi âm' : 'Nói câu hỏi (tiếng Việt)'}
                  className={cn('w-8 h-8 rounded-lg flex items-center justify-center border transition-colors shrink-0',
                    listening ? 'bg-[#FAD7D7] border-[#9F1D1D] text-[#7A1010]' : 'bg-white border-[#BFA66A] text-[#3F3F3F] hover:bg-[#FFF9E8]')}
                >
                  {listening ? <MicOff size={14} /> : <Mic size={14} />}
                </button>
              )}
              <button
                type="button"
                onClick={() => ask()}
                disabled={!input.trim() || sending}
                aria-label="Gửi câu hỏi"
                className="w-8 h-8 rounded-lg flex items-center justify-center bg-[#111111] text-[#D9B86C] disabled:opacity-40 shrink-0"
              >
                <Send size={14} />
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
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#D6C79F] shrink-0">
                <ShieldCheck size={13} className="text-[#B88719]" />
                <span className="text-[12px] font-semibold text-[#111111]">Con số này ở đâu ra</span>
                <ChevronRight size={12} className="text-[#777777] ml-auto" />
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
