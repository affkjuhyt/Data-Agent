/**
 * Phân tích › Hỏi đáp — "P01 · Hỏi đáp" (2296:18429, UC-01-01/03/04, UC-10-01).
 *
 * Bordered page header, then the conversation: the question right-aligned on subtle, each answer
 * with "Hệ thống hiểu là", the text, chart and tables, the trust label and its links, follow-up
 * chips, and the "Hỏi tiếp…" box under it. "Cách tính" opens the 420px panel on the right
 * (2296:18631, UC-01-02); "Xuất" and "Báo sai" open their dialogs (UC-01-06, UC-01-05).
 *
 * Questions go to POST /v1/questions and are followed on their stream (useQuestionStream); the
 * agent that answers is chosen by the system, so the Figma picker "Agent trả lời" is not drawn
 * (A3 has no agent field). The thread is durable on the server: `/analysis/ask/<conversation_id>`
 * reopens it (R1-13). Bookmarks stay in localStorage; "Hỏi gần đây" comes from GET /v1/conversations.
 *
 * Not drawn yet: "Mở dạng notebook", "Chia sẻ", editing a "Hệ thống hiểu là" chip to ask again
 * (UC-01-03), per-number lineage.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Menu } from '../../design-system/Menu';
import { SuggestionChip } from '../../design-system/SuggestionChip';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import type { Feedback } from '../../lib/v1/answerReports';
import { listConversations, saveAnswerToSession } from '../../lib/v1/questions';
import { hasReceipt } from './answerMessage';
import { useQuestionStream } from './hooks/useQuestionStream';
import { AnswerBlock } from './components/AnswerBlock';
import { AnswerTheme } from './components/AnswerTheme';
import { EvidencePanel } from './components/EvidencePanel';
import { ReportDialog } from './components/ReportDialog';
import { SaveToKnowledgeDialog } from '../knowledge/SaveToKnowledgeDialog';
import { SaveToSessionDialog } from '../explore/SaveToSessionDialog';
import { ReceiptDrawer } from '../answer-receipt/AnswerReceiptPage';
import { requestPage } from '../documents/focusPage';
import type { AskMessage, Citation } from './types';

/* ─── per-viewer bookmarks (not the conversation thread) ─────────── */

interface StoredQuestion { q: string; at: string }
const SAVED_KEY = 'dataagent.ask.saved';

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
  const { user, navigate, routeId } = useAppState();
  const toast = useToast();
  const openCitation = (c: Citation) => {
    if (!c.doc_id) return;
    requestPage(c.doc_id, c.page);
    navigate('data', 'DOCUMENT', c.doc_id);
  };
  const [input, setInput] = useState('');
  const [saved, setSaved] = useState<StoredQuestion[]>(() => readList(SAVED_KEY));
  const [recent, setRecent] = useState<{ id: string; title: string }[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reportFor, setReportFor] = useState<AskMessage | null>(null);
  const [receiptFor, setReceiptFor] = useState<string | null>(null);
  const [knowledgeFor, setKnowledgeFor] = useState<string | null>(null);
  const [sessionFor, setSessionFor] = useState<AskMessage | null>(null);
  const [savedToSession, setSavedToSession] = useState<Record<string, true>>({});
  const [reported, setReported] = useState<Record<string, Feedback>>({});
  const [listening, setListening] = useState(false);
  const recogRef = useRef<Recognition | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // The agent that answers is chosen by the system: POST /v1/questions has no agent field (A3).
  const {
    messages,
    sending,
    hydrating,
    conversationId,
    send,
    answerClarification,
    confirmUnderstanding,
    stop,
    hydrate,
  } = useQuestionStream();

  useEffect(() => { bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' }); }, [messages]);

  // Reopen from /analysis/ask/<conversation_id> (R1-13).
  useEffect(() => {
    if (routeId) void hydrate(routeId);
  }, [routeId, hydrate]);

  // Put the conversation id in the URL once the first question is accepted.
  useEffect(() => {
    if (!conversationId || routeId === conversationId) return;
    navigate('analysis', 'ASK', conversationId);
  }, [conversationId, routeId, navigate]);

  // Recent threads from the server (not localStorage).
  useEffect(() => {
    let cancelled = false;
    listConversations(8)
      .then((page) => {
        if (cancelled) return;
        setRecent(
          page.items.map((c) => ({
            id: c.id,
            title: (c.title?.trim() || 'Hội thoại không tiêu đề'),
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

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
    if (!q || sending || hydrating) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSelectedId(null);
    void send(q);
  };

  // A question handed over from elsewhere (Trang chủ's question bar, ⌘K) arrives as ?q=.
  // It is asked once, then dropped from the URL so a reload does not ask it again.
  const handedOver = useRef(false);
  useEffect(() => {
    if (handedOver.current || routeId) return;
    handedOver.current = true;
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q');
    if (!q) return;
    params.delete('q');
    const rest = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`);
    ask(q);
  }, [routeId]);

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
  const description = [
    `Phiên của ${user.name}`,
    `${questions.length} câu hỏi`,
    'hệ thống tự chọn agent trả lời',
  ].join(' · ');

  return (
    <AnswerTheme mode="light" className="flex h-full min-h-0 flex-col">
      {/* "v3.1 · Mobile · Hỏi đáp" (2231:14983): under 768px the page is titled "Hỏi" and the
          agent picker, saved questions and breadcrumb give way to the conversation. */}
      <h1 className="px-da-lg pb-da-xs pt-da-md text-foreground da-heading-1 min-[768px]:hidden">Hỏi</h1>
      <PageHeader
        bordered
        className="max-[767px]:hidden"
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
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        <section aria-label="Hội thoại" className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto px-da-4xl pt-[20px] max-[767px]:px-da-lg max-[767px]:pt-da-md">
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

              {messages.length === 0 && !hydrating && (saved.length > 0 || recent.length > 0) && (
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
                  {recent.length > 0 && (
                    <section aria-label="Hỏi gần đây" className="flex flex-col gap-da-sm">
                      <h2 className="m-0 text-muted-foreground da-overline uppercase">Hỏi gần đây</h2>
                      <ul className="m-0 flex list-none flex-col p-0">
                        {recent.map(h => (
                          <li key={h.id}>
                            <button
                              type="button"
                              onClick={() => navigate('analysis', 'ASK', h.id)}
                              title="Mở lại hội thoại"
                              className="w-full truncate rounded-sm px-da-md py-[5px] text-left text-muted-foreground da-body-small hover:bg-subtle hover:text-foreground"
                            >
                              {h.title}
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
                    onReport={msg.answerId && msg.content ? () => setReportFor(msg) : undefined}
                    onSaveKnowledge={msg.answerId && msg.content ? () => setKnowledgeFor(msg.answerId!) : undefined}
                    onSaveToSession={msg.answerId && msg.content ? () => setSessionFor(msg) : undefined}
                    savedToSession={!!(msg.answerId && savedToSession[msg.answerId])}
                    /* The stream gives no server answer id yet (docs/v3/05-ui-v3.md §4), so this is the
                       client message id: the receipt page shows "chưa có biên nhận" until it does. */
                    onOpenReceipt={hasReceipt(msg) ? () => setReceiptFor(msg.answerId!) : undefined}
                    onOpenCitation={openCitation}
                    reported={!!(msg.answerId && reported[msg.answerId])}
                    onStop={msg.status === 'streaming' ? stop : undefined}
                    onConfirmUnderstanding={choice => void confirmUnderstanding(msg.id, choice)}
                    onEditUnderstanding={() => {
                      setInput(questionFor(msg.id));
                      textareaRef.current?.focus();
                    }}
                  />
                </article>
              ))}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="px-da-4xl pb-da-xl pt-da-md max-[767px]:border-t max-[767px]:border-border max-[767px]:p-[10px]">
            <div className="mx-auto flex max-w-[1136px] items-end gap-da-md rounded-md border border-border-strong bg-card py-[6px] pl-[14px] pr-da-sm focus-within:border-ring max-[767px]:items-center max-[767px]:border-0 max-[767px]:p-0">
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
                className="max-h-[200px] min-h-8 flex-1 resize-none bg-transparent py-1.5 text-foreground outline-none da-body placeholder:text-muted-foreground focus-visible:shadow-none max-[767px]:rounded-lg max-[767px]:bg-subtle max-[767px]:p-[10px] max-[767px]:text-[13px] max-[767px]:leading-[18px]"
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
                className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-subtle disabled:text-muted-foreground max-[767px]:h-9 max-[767px]:w-auto max-[767px]:px-da-lg max-[767px]:text-[13px] max-[767px]:font-medium"
              >
                <Icon name="arrow-up" className="max-[767px]:hidden" />
                <span className="min-[768px]:hidden">Gửi</span>
              </button>
            </div>
          </div>
        </section>

        {panelOpen && (
          <aside
            aria-label="Cách tính"
            className="w-[420px] max-w-[45%] shrink-0 overflow-y-auto border-l border-border bg-background px-da-2xl pb-da-2xl pt-[20px] max-[767px]:fixed max-[767px]:inset-0 max-[767px]:z-[var(--z-drawer)] max-[767px]:w-full max-[767px]:max-w-full max-[767px]:px-da-lg"
          >
            <EvidencePanel message={selected} onClose={() => setPanelOpen(false)} />
          </aside>
        )}
      </div>

      {/* The receipt opens over the conversation (Figma 2420:15098), so the turns stay. */}
      <ReceiptDrawer answerId={receiptFor} onClose={() => setReceiptFor(null)} />
      {knowledgeFor && (
        <SaveToKnowledgeDialog answerId={knowledgeFor} onClose={() => setKnowledgeFor(null)} />
      )}
      {sessionFor?.answerId && (
        <SaveToSessionDialog
          onClose={() => setSessionFor(null)}
          onSave={(workspaceId) =>
            saveAnswerToSession(workspaceId, {
              answerId: sessionFor.answerId!,
              title: questionFor(sessionFor.id) || sessionFor.content.slice(0, 80),
            })
          }
          onSaved={(workspaceId, title) => {
            const answerId = sessionFor.answerId!;
            setSessionFor(null);
            setSavedToSession((prev) => ({ ...prev, [answerId]: true }));
            toast({
              tone: 'success',
              message: `Đã thêm vào ${title}`,
              action: {
                label: 'Mở phiên',
                onClick: () => navigate('analysis', 'SESSIONS', workspaceId),
              },
            });
          }}
        />
      )}
      <ReportDialog
        open={!!reportFor}
        target={reportFor?.answerId ? {
          answerId: reportFor.answerId,
          question: questionFor(reportFor.id),
          answer: reportFor.content,
        } : null}
        onClose={() => setReportFor(null)}
        onReported={feedback => setReported(prev => ({ ...prev, [feedback.answer_id]: feedback }))}
      />
    </AnswerTheme>
  );
}
