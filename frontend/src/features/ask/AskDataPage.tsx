/**
 * Phân tích › Hỏi đáp — the chat (docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md).
 *
 * A conversation panel on the left (new conversation, search, recents); in the centre either the
 * empty chat (greeting, question box, starters, recent chats) or the thread: each question, then
 * the agent's turn — the timeline of its steps, each one opening to what it did, then the answer
 * drawn block by block, its trust label, its next questions and its footer. The question box
 * takes "@" for a metric or dimension and "/" for the chat commands.
 *
 * Questions go to POST /v1/questions and are followed on their stream (useQuestionStream). The
 * thread is durable on the server: `/analysis/ask/<conversation_id>` reopens it (R1-13).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { useToast } from '../../design-system/Toast';
import type { Feedback } from '../../lib/v1/answerReports';
import { getExploreModel } from '../../lib/v1/explore';
import { saveAnswerToSession } from '../../lib/v1/questions';
import { listCollections } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { documentStarterQuestions, starterQuestions } from '../../lib/sampleQuestion';
import { ReceiptDrawer } from '../answer-receipt/AnswerReceiptPage';
import { requestPage } from '../documents/focusPage';
import { SaveToSessionDialog } from '../explore/SaveToSessionDialog';
import { SaveToKnowledgeDialog } from '../knowledge/SaveToKnowledgeDialog';
import { layoutForCommand } from './answerLayout';
import { commandHelp, commandPrompt, parseChatInput } from './chatCommands';
import { ChatComposer } from './chat/ChatComposer';
import { ConversationPanel, useRecentChats } from './chat/ConversationPanel';
import { EmptyChat } from './chat/EmptyChat';
import { Thread } from './chat/Thread';
import { AnswerTheme } from './components/AnswerTheme';
import { CitationPassagePanel } from './components/CitationPassagePanel';
import { ReportDialog } from './components/ReportDialog';
import { csvFileName, downloadCsv, toCsv } from './exportCsv';
import { useQuestionStream } from './hooks/useQuestionStream';
import type { AskMessage, Citation } from './types';

export default function AskDataPage() {
  const { user, navigate, routeId } = useAppState();
  const toast = useToast();
  // Starters from the Data Space's own certified metrics, plus document hints when collections exist.
  const model = useV1(['getExploreModel'], getExploreModel);
  const collections = useV1(['listCollections'], listCollections);
  const starters = useMemo(() => {
    const metric = starterQuestions(model.data, 3);
    const docs = documentStarterQuestions((collections.data?.items?.length ?? 0) > 0);
    return [...metric, ...docs];
  }, [model.data, collections.data]);

  const [reportFor, setReportFor] = useState<AskMessage | null>(null);
  const [receiptFor, setReceiptFor] = useState<string | null>(null);
  const [knowledgeFor, setKnowledgeFor] = useState<string | null>(null);
  const [sessionFor, setSessionFor] = useState<AskMessage | null>(null);
  const [reported, setReported] = useState<Record<string, Feedback>>({});
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

  // The agent that answers is chosen by the system: POST /v1/questions has no agent field (A3).
  const {
    messages,
    sending,
    queued = [],
    hydrating,
    conversationId,
    send,
    answerClarification,
    retry,
    stop,
    addNotice,
    hydrate,
    resetConversation,
  } = useQuestionStream();
  const recent = useRecentChats(conversationId);

  // Reopen from /analysis/ask/<conversation_id> (R1-13).
  useEffect(() => {
    if (routeId) void hydrate(routeId);
  }, [routeId, hydrate]);

  // Put the conversation id in the URL once the first question is accepted.
  useEffect(() => {
    if (!conversationId || routeId === conversationId) return;
    navigate('analysis', 'ASK', conversationId);
  }, [conversationId, routeId, navigate]);

  const ask = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || hydrating) return;
      const parsed = parseChatInput(text);
      if (parsed.command?.name === 'new') {
        resetConversation();
        navigate('analysis', 'ASK');
        return;
      }
      if (parsed.command?.name === 'help') {
        addNotice(commandHelp());
        return;
      }
      if (parsed.command?.name === 'status') {
        addNotice(`Đang xử lý: ${sending ? 'có' : 'không'} · Đang chờ: ${queued.length}`);
        return;
      }
      if (parsed.command && !parsed.argument) {
        addNotice(`Dùng ${parsed.command.label} kèm nội dung cần phân tích.`);
        return;
      }
      const q = parsed.command ? commandPrompt(parsed.command.name, parsed.argument) : parsed.argument;
      const layout = parsed.command ? layoutForCommand(parsed.command.name) : undefined;
      void (layout ? send(q, layout) : send(q));
    },
    [hydrating, resetConversation, navigate, addNotice, sending, queued.length, send],
  );

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
  }, [routeId, ask]);

  const openCitation = (c: Citation) => {
    if (c.type === 'document') {
      setSelectedCitation(c);
      return;
    }
    if (!c.doc_id) return;
    requestPage(c.doc_id, c.page);
    navigate('data', 'DOCUMENT', c.doc_id);
  };
  const openCitationViewer = (c: Citation) => {
    if (!c.doc_id) return;
    requestPage(c.doc_id, c.page);
    navigate('data', 'DOCUMENT', c.doc_id);
  };
  const newConversation = () => {
    resetConversation();
    navigate('analysis', 'ASK');
  };
  const exportCsv = (m: AskMessage) => {
    const run = m.evidence?.runs[0];
    if (!run) return;
    downloadCsv(csvFileName(m.questionText ?? 'cau-tra-loi'), toCsv(run.columns, run.rows));
  };

  const composer = (
    <ChatComposer sending={sending} onSend={ask} onStop={stop} model={model.data} autoFocus />
  );
  const title = messages.find((m) => m.role === 'user')?.content ?? 'Hỏi đáp';

  return (
    <AnswerTheme mode="light" className="flex h-full min-h-0">
      <ConversationPanel recent={recent} activeId={conversationId} onNew={newConversation} onOpen={(id) => navigate('analysis', 'ASK', id)} />
      <section aria-label="Hỏi đáp" className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-da-md border-b border-border px-da-xl py-da-md">
          <h1 className="m-0 min-w-0 flex-1 truncate text-foreground da-heading-3">{title}</h1>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={newConversation}
              className="text-primary da-body-strong hover:underline min-[1024px]:hidden"
            >
              Cuộc trò chuyện mới
            </button>
          )}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {messages.length === 0 && !hydrating ? (
            <EmptyChat
              name={user.name}
              starters={starters}
              recent={recent}
              composer={composer}
              onAsk={ask}
              onOpen={(id) => navigate('analysis', 'ASK', id)}
            />
          ) : (
            <Thread
              messages={messages}
              busy={sending}
              reported={reported}
              handlers={{
                onRetry: (id) => void retry(id),
                onReport: (m) => setReportFor(m),
                onReceipt: (answerId) => setReceiptFor(answerId),
                onSaveKnowledge: (answerId) => setKnowledgeFor(answerId),
                onSaveSession: (m) => setSessionFor(m),
                onAsk: ask,
                onAnswerClarification: (id, reply) => void answerClarification(id, reply),
                onOpenCitation: openCitation,
                onOpenForecast: (forecastId) => navigate('analysis', 'FORECAST_JOB', forecastId),
                onExportCsv: exportCsv,
              }}
            />
          )}
        </div>
        {(messages.length > 0 || hydrating) && (
          <div className="mx-auto w-full max-w-[880px] px-da-xl pb-da-xl pt-da-sm">
            {queued.length > 0 && (
              <p className="m-0 mb-da-sm truncate text-muted-foreground da-caption">
                Đang chờ {queued.length} câu: {queued.join(' · ')}
              </p>
            )}
            {composer}
          </div>
        )}
      </section>
      <CitationPassagePanel
        citation={selectedCitation}
        onOpen={openCitationViewer}
        onClose={() => setSelectedCitation(null)}
      />

      {/* The receipt opens over the conversation, so the turns stay. */}
      <ReceiptDrawer answerId={receiptFor} onClose={() => setReceiptFor(null)} />
      {knowledgeFor && <SaveToKnowledgeDialog answerId={knowledgeFor} onClose={() => setKnowledgeFor(null)} />}
      {sessionFor?.answerId && (
        <SaveToSessionDialog
          onClose={() => setSessionFor(null)}
          onSave={(workspaceId) =>
            saveAnswerToSession(workspaceId, {
              answerId: sessionFor.answerId!,
              title: sessionFor.questionText || sessionFor.content.slice(0, 80),
            })
          }
          onSaved={(workspaceId, sessionTitle) => {
            setSessionFor(null);
            toast({
              tone: 'success',
              message: `Đã thêm vào ${sessionTitle}`,
              action: { label: 'Mở phiên', onClick: () => navigate('analysis', 'SESSIONS', workspaceId) },
            });
          }}
        />
      )}
      <ReportDialog
        open={!!reportFor}
        target={
          reportFor?.answerId
            ? { answerId: reportFor.answerId, question: reportFor.questionText ?? '', answer: reportFor.content }
            : null
        }
        onClose={() => setReportFor(null)}
        onReported={(feedback) => setReported((prev) => ({ ...prev, [feedback.answer_id]: feedback }))}
      />
    </AnswerTheme>
  );
}
