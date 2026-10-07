/**
 * How one assistant message is built from the stream, as a pure function of
 * (state, frame) so the frame order, old and new frame kinds, and frames to
 * ignore are testable without a network. useAgentStream feeds it; `conv_ids`
 * stays in the hook (it updates the hook's thread, not the message).
 *
 * Old frames behave exactly as before; agent-run frames (run, plan, step,
 * block, verification, lineage) are kept on the message for the screens that
 * draw them. Unknown frames change nothing.
 */
import type { SseEvent } from './sse';
import type { AskMessage } from './types';

interface AskStreamState {
  message: AskMessage;
  /** the answer text so far; a reset or a refusal empties it */
  buffer: string;
  sessionId?: string;
  /** responder options, applied when the stream ends */
  options: string[] | null;
  /** the orchestration stopped to ask: the answer goes to /resume */
  interrupted: boolean;
  /** the question this turn works on, for a follow-up clarification card */
  query: string;
}

export const startStream = (message: AskMessage, query: string): AskStreamState => ({
  message,
  buffer: '',
  options: null,
  interrupted: false,
  query,
});

const withMessage = (state: AskStreamState, patch: Partial<AskMessage>): AskStreamState => ({
  ...state,
  message: { ...state.message, ...patch },
});

export function streamReducer(state: AskStreamState, e: SseEvent): AskStreamState {
  const m = state.message;
  switch (e.type) {
    case 'token': {
      const buffer = state.buffer + e.text;
      return { ...withMessage(state, { content: buffer }), buffer };
    }
    case 'reset':
      // The streamed answer was rejected; its replacement follows.
      return { ...withMessage(state, { content: '' }), buffer: '' };
    case 'session':
      return { ...withMessage(state, { sessionId: e.sessionId }), sessionId: e.sessionId };
    case 'clarification':
      // Orchestrator graph interrupted: the answer must go to /resume.
      return {
        ...withMessage(state, {
          clarification: {
            kind: 'resume',
            sessionId: e.sessionId || state.sessionId || '',
            questions: e.details,
          },
        }),
        interrupted: true,
      };
    case 'evidence':
      // Deterministic questions from the runtime: render them as a card
      // whose answer is simply the next turn (slots merge server-side).
      return withMessage(state, {
        evidence: e.evidence,
        ...(e.evidence.clarifications.length && !m.clarification
          ? {
              clarification: {
                kind: 'followup' as const,
                questions: e.evidence.clarifications,
                query: state.query,
              },
            }
          : {}),
      });
    case 'options':
      return { ...state, options: e.options };
    case 'chart':
      return withMessage(state, { chartSpecs: e.specs });
    case 'result_set':
      return withMessage(state, {
        resultSets: [
          ...(m.resultSets ?? []).filter((r) => r.result_id !== e.resultSet.result_id),
          e.resultSet,
        ],
      });
    case 'citations':
      return withMessage(state, { citations: e.citations });
    case 'suggestions':
      return withMessage(state, { suggestions: e.suggestions });
    case 'pending_review':
      return withMessage(state, {
        pendingReview: { convId: e.convId, payload: e.payload as never },
      });
    case 'refusal':
      // In place of an answer, never beside one: anything streamed is dropped.
      return { ...withMessage(state, { content: '', refusal: e.refusal }), buffer: '' };
    case 'error':
      return withMessage(state, { content: m.content || e.message, status: 'error' });
    case 'run': {
      // Start and end frames carry different fields of the same run.
      const run = { ...m.run, ...e.run };
      return withMessage(state, {
        run,
        ...(run.status === 'error' ? { status: 'error' as const } : {}),
      });
    }
    case 'plan':
      return withMessage(state, { plan: e.plan });
    case 'step':
      return withMessage(state, {
        steps: { ...m.steps, [e.step.node_id]: { ...m.steps?.[e.step.node_id], ...e.step } },
      });
    case 'block':
      return withMessage(state, { blocks: [...(m.blocks ?? []), e.block] });
    case 'verification':
      return withMessage(state, { verification: e.verification });
    case 'lineage':
      return withMessage(state, { lineage: e.lineage });
    default:
      return state;
  }
}

/** The message once the stream has ended normally. */
export function finishStream(state: AskStreamState, latencyMs: number): AskMessage {
  const next: AskMessage = {
    ...state.message,
    latencyMs,
    status: state.message.status === 'error' ? 'error' : 'done',
  };
  // A responder <OPTIONS> block without structured clarifications:
  // still a question the user should be able to answer in one click.
  if (state.options && !next.clarification) {
    next.clarification = {
      kind: 'followup',
      questions: [{ text: '', options: state.options }],
      query: state.query,
    };
  }
  if (state.interrupted && !next.content) {
    next.content =
      next.clarification?.questions
        .map((x) => x.text)
        .filter(Boolean)
        .join('\n') || '';
  }
  return next;
}
