/**
 * Phân tích › Hỏi đáp — "P01 · Hỏi đáp" (2296:18429, UC-01-01/03/04, UC-10-01).
 *
 * Bordered page header, then the conversation: the question right-aligned on subtle, each answer
 * with "Hệ thống hiểu là", the text, chart and tables, the trust label and its links, follow-up
 * chips, and the "Hỏi tiếp…" box under it. "Cách tính" opens the 420px panel on the right
 * (2296:18631, UC-01-02); "Xuất" and "Báo sai" open their dialogs (UC-01-06, UC-01-05).
 *
 * Answers stream from the orchestrator (useAgentStream); the only client-side state is per-viewer
 * convenience (saved questions, recent questions) in localStorage.
 *
 * Not drawn, no backend yet: "Mở dạng notebook", "Chia sẻ", "Thêm vào phiên" (no session API on
 * this stream), editing a "Hệ thống hiểu là" chip to ask again (UC-01-03), per-number lineage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Menu } from '../../design-system/Menu';
import { Select } from '../../design-system/Select';
import { SuggestionChip } from '../../design-system/SuggestionChip';
import { cn } from '../../lib/utils';
import type { AnswerReport } from '../../lib/v1/answerReports';
import { fetchAgents } from './api';
import { useAgentStream } from './hooks/useAgentStream';
import { AnswerBlock } from './components/AnswerBlock';
import { AnswerTheme } from './components/AnswerTheme';
import { EvidencePanel } from './components/EvidencePanel';
import { ReportDialog } from './components/ReportDialog';
import { SaveToKnowledgeDialog } from '../knowledge/SaveToKnowledgeDialog';
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

const AUTO = 'auto';

/* ─── page ────────────────────────────────────────────────────────── */

export default function AskDataPage() {
  const { user } = useAppState();
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentsFailed, setAgentsFailed] = useState(false);
  const [scope, setScope] = useState<string>(AUTO);
  const [input, setInput] = useState('');
  const [saved, setSaved] = useState<StoredQuestion[]>(() => readList(SAVED_KEY));
  const [history, setHistory] = useState<StoredQuestion[]>(() => readList(HISTORY_KEY));
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reportFor, setReportFor] = useState<AskMessage | null>(null);
  const [knowledgeFor, setKnowledgeFor] = useState<string | null>(null);
  const [reported, setReported] = useState<Record<string, AnswerReport>>({});
  const [listening, setListening] = useState(false);
  const recogRef = useRef<Recognition | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { messages, sending, send, answerClarification, stop } = useAgentStream({
    userId: user.id,
    agentId: scope === AUTO ? null : scope,
  });

  useEffect(() => {
    let alive = true;
    fetchAgents()
      .then(list => { if (alive) setAgents(list); })
      .catch(() => { if (alive) setAgentsFailed(true); /* scope stays automatic */ })
      .finally(() => { if (alive) setAgentsLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => { bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' }); }, [messages]);

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

  // A question handed over from elsewhere (Trang chủ's question bar, ⌘K) arrives as ?q=.
  // It is asked once, then dropped from the URL so a reload does not ask it again.
  const handedOver = useRef(false);
  useEffect(() => {
    if (handedOver.current) return;
    handedOver.current = true;
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q');
    if (!q) return;
    params.delete('q');
    const rest = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`);
    ask(q);
  }, []);

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
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  const questions = messages.filter(m => m.role === 'user');
  const title = questions[0]?.content ?? 'Hỏi đáp';
  const scopeOptions = [
    { value: AUTO, label: 'Tự động chọn agent' },
    ...agents.map(a => ({ value: a.id, label: a.label })),
  ];
  const scopeNote = agentsLoading
    ? 'đang tải danh sách agent'
    : agentsFailed
      ? 'không tải được danh sách agent, câu hỏi được chuyển tự động'
      : scope === AUTO ? 'hệ thống tự chọn agent trả lời' : agents.find(a => a.id === scope)?.description;
  const description = [
    `Phiên của ${user.name}`,
    `${questions.length} câu hỏi`,
    scopeNote,
  ].filter(Boolean).join(' · ');

  return (
    <AnswerTheme mode="light" className="flex h-full min-h-0 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Hỏi đáp']}
        title={<span className="line-clamp-1 break-all">{title}</span>}
        description={description}
        actions={
          <>
            {sending && <Button kind="ghost" icon="x" onClick={stop}>Dừng</Button>}
            {saved.length > 0 && (
              <Menu
                label="Câu hỏi đã lưu"
                trigger={<Icon name="bookmark" label="Câu hỏi đã lưu" />}
                items={saved.slice(0, 12).map(s => ({ label: s.q, onSelect: () => ask(s.q) }))}
              />
            )}
            <div className="w-56">
              <Select
                label="Agent trả lời"
                options={scopeOptions}
                value={scope}
                onChange={setScope}
                disabled={agentsLoading}
              />
            </div>
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        <section aria-label="Hội thoại" className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-da-4xl pt-[20px]">
            <div className="mx-auto flex max-w-[1136px] flex-col gap-da-xl pb-da-xl">
              {messages.length === 0 && (
                <EmptyState
                  icon="chat"
                  title="Bắt đầu bằng một câu hỏi rõ ràng"
                  description="Thiếu kỳ, chỉ số hoặc phạm vi dữ liệu thì hệ thống hỏi lại trước khi chạy truy vấn."
                  action={
                    <div className="flex flex-wrap justify-center gap-da-md">
                      {EXAMPLES.map(e => (
                        <SuggestionChip key={e} label={e} onSelect={() => { setInput(e); textareaRef.current?.focus(); }} />
                      ))}
                    </div>
                  }
                />
              )}

              {messages.length === 0 && (saved.length > 0 || history.length > 0) && (
                <div className="grid gap-da-2xl md:grid-cols-2">
                  {saved.length > 0 && (
                    <section aria-label="Câu hỏi đã lưu" className="flex flex-col gap-da-sm">
                      <h2 className="m-0 text-muted-foreground da-overline uppercase">Câu hỏi đã lưu</h2>
                      <ul className="m-0 flex list-none flex-col p-0">
                        {saved.map(s => (
                          <li key={s.q} className="group flex items-center gap-da-sm rounded-sm px-da-md py-[5px] hover:bg-subtle">
                            <button type="button" onClick={() => ask(s.q)} disabled={sending} title="Hỏi lại" className="min-w-0 flex-1 truncate text-left text-foreground da-body-small">
                              {s.q}
                            </button>
                            <button type="button" onClick={() => removeSaved(s.q)} aria-label={`Bỏ lưu: ${s.q}`} className="text-muted-foreground opacity-0 hover:text-error focus-visible:opacity-100 group-hover:opacity-100">
                              <Icon name="trash" size={14} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {history.length > 0 && (
                    <section aria-label="Hỏi gần đây" className="flex flex-col gap-da-sm">
                      <h2 className="m-0 text-muted-foreground da-overline uppercase">Hỏi gần đây</h2>
                      <ul className="m-0 flex list-none flex-col p-0">
                        {history.slice(0, 8).map(h => (
                          <li key={h.q}>
                            <button
                              type="button"
                              onClick={() => { setInput(h.q); textareaRef.current?.focus(); }}
                              title="Đưa vào ô hỏi"
                              className="w-full truncate rounded-sm px-da-md py-[5px] text-left text-muted-foreground da-body-small hover:bg-subtle hover:text-foreground"
                            >
                              {h.q}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                </div>
              )}

              {messages.map(msg => msg.role === 'user' ? (
                <div key={msg.id} className="flex justify-end">
                  <p className="m-0 max-w-[80%] whitespace-pre-wrap rounded-md bg-subtle px-[14px] py-[10px] text-foreground da-body">
                    {msg.content}
                  </p>
                </div>
              ) : (
                <article
                  key={msg.id}
                  aria-label="Câu trả lời"
                  className={cn(
                    'rounded-md',
                    panelOpen && selected?.id === msg.id && 'outline-2 outline-offset-8 outline-accent',
                  )}
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
                    onRetry={() => ask(questionFor(msg.id))}
                    onRephrase={() => {
                      setInput(questionFor(msg.id));
                      textareaRef.current?.focus();
                    }}
                    onReport={msg.content ? () => setReportFor(msg) : undefined}
                    onSaveKnowledge={msg.content ? () => setKnowledgeFor(msg.id) : undefined}
                    reported={!!reported[msg.id]}
                    onStop={msg.status === 'streaming' ? stop : undefined}
                  />
                </article>
              ))}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="px-da-4xl pb-da-xl pt-da-md">
            <div className="mx-auto flex max-w-[1136px] items-end gap-da-md rounded-md border border-border-strong bg-card py-[6px] pl-[14px] pr-da-sm focus-within:border-ring">
              <textarea
                ref={textareaRef}
                value={input}
                rows={1}
                onChange={e => { setInput(e.target.value); adjustHeight(); }}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); ask(); }
                }}
                placeholder={sending ? 'Đang trả lời… bạn vẫn gõ được câu tiếp theo' : messages.length ? 'Hỏi tiếp…' : 'Hỏi về dữ liệu của bạn…'}
                aria-label="Câu hỏi"
                className="max-h-[200px] min-h-8 flex-1 resize-none bg-transparent py-1.5 text-foreground outline-none da-body placeholder:text-muted-foreground focus-visible:shadow-none"
              />
              {micAvailable && (
                <button
                  type="button"
                  onClick={toggleMic}
                  aria-pressed={listening}
                  aria-label={listening ? 'Dừng ghi âm' : 'Nói câu hỏi'}
                  title={listening ? 'Dừng ghi âm' : 'Nói câu hỏi'}
                  className={cn(
                    'inline-flex size-8 shrink-0 items-center justify-center rounded-md hover:bg-subtle',
                    listening ? 'bg-error-subtle text-error' : 'text-muted-foreground',
                  )}
                >
                  <Icon name="mic" />
                </button>
              )}
              <button
                type="button"
                onClick={() => ask()}
                disabled={!input.trim() || sending}
                aria-label="Gửi câu hỏi"
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-subtle disabled:text-muted-foreground"
              >
                <Icon name="arrow-up" />
              </button>
            </div>
          </div>
        </section>

        {panelOpen && (
          <aside
            aria-label="Cách tính"
            className="w-[420px] max-w-[45%] shrink-0 overflow-y-auto border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px]"
          >
            <EvidencePanel message={selected} onClose={() => setPanelOpen(false)} />
          </aside>
        )}
      </div>

      {knowledgeFor && (
        <SaveToKnowledgeDialog answerId={knowledgeFor} onClose={() => setKnowledgeFor(null)} />
      )}
      <ReportDialog
        open={!!reportFor}
        target={reportFor ? {
          messageId: reportFor.id,
          sessionId: reportFor.sessionId,
          question: questionFor(reportFor.id),
          answer: reportFor.content,
        } : null}
        onClose={() => setReportFor(null)}
        onReported={report => setReported(prev => ({ ...prev, [report.message_id]: report }))}
      />
    </AnswerTheme>
  );
}
