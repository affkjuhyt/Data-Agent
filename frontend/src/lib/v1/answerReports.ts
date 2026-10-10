import { v1Send } from './client';

/*
 * "Báo sai câu trả lời" (UC-01-05, Figma 2239:15384): the reader says an answer is wrong; the
 * report becomes a proposal in Cần bạn for whoever owns the metric. Reporting never changes a
 * number by itself — it goes through review.
 *
 * The system design has no resource for this yet, so the type and endpoint are a PROPOSAL for
 * the backend; the mock (mocks/answerReports.ts) serves it.
 */
type Uuid = string;
type Timestamp = string;

/** PROPOSAL: what is wrong, the four choices of "Sai ở đâu?". */
export type AnswerReportKind = 'misread_question' | 'wrong_number' | 'wrong_source' | 'incomplete';

/** PROPOSAL: body of POST /v1/answer-reports. */
export interface NewAnswerReport {
  /** the orchestrator/agent conversation the answer belongs to, when the stream gave one */
  session_id?: string;
  /** client id of the answer in the conversation */
  message_id: string;
  question: string;
  /** the answer text as the reader saw it */
  answer: string;
  kind: AnswerReportKind;
  /** "Con số hoặc cách hiểu đúng" */
  correction?: string;
  /** "Gửi người phụ trách dữ liệu xem; nếu đúng, thêm vào bộ câu hỏi chuẩn" */
  send_to_steward: boolean;
}

/** PROPOSAL: the stored report; `proposal_id` is the item it opened in Cần bạn. */
export interface AnswerReport extends NewAnswerReport {
  id: Uuid;
  status: 'under_review' | 'accepted' | 'rejected';
  proposal_id?: Uuid | null;
  created_at: Timestamp;
}

/** PROPOSAL: POST /v1/answer-reports */
export const reportAnswer = (body: NewAnswerReport) =>
  v1Send<AnswerReport>('POST', '/answer-reports', body);
