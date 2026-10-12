import { pathId, v1Get, v1Send, type Page } from './client';
import type { DocumentDetail, Tone } from './sources';

/*
 * P05 Tài liệu phi cấu trúc beyond the collection list (which lives in sources.ts): the document
 * library, the document / email / call viewers, the quality queue, document validity, answers
 * from documents and extraction schemas. Paths follow "Đặc tả API", Document service:
 * /v1/documents, /v1/documents/{id}/pages/{n}, /v1/documents/{id}/versions, /v1/quality-issues,
 * /v1/extraction-schemas, /v1/collections. Everything the screens need beyond those rows is
 * marked PROPOSAL, as in types.ts, and served by mocks/documents.ts until the backend has it.
 */

type Uuid = string;
type Timestamp = string;

const ALL = { page_size: 500 };


/* ─── Tệp & tài liệu: the document library ────────────────────────────── */

export type DocumentCategory = 'regulation' | 'internal' | 'contract' | 'correspondence';

/** A row of "v3.1 · Phi cấu trúc · Tệp & tài liệu" (GET /v1/documents). */
export interface DocumentRow {
  id: Uuid;
  title: string;
  category: DocumentCategory;
  /** PROPOSAL: "Quy định", "Nội bộ". */
  category_label: string;
  /** PROPOSAL: "Từ 01/01/2027", "Đang hiệu lực"; `ended` draws "Hết hiệu lực 30/09" as a badge. */
  validity: { tone: Tone; text: string; ended: boolean } | null;
  /** PROPOSAL: clauses the agent extracted. */
  clause_count: number | null;
  /** PROPOSAL: "NPL_RATIO · đề xuất v5" — what the document is linked to. */
  links_text: string | null;
  /** PROPOSAL: set while the pre-processing runs ("Đang xử lý" tab). */
  processing: { step_text: string; progress: number; started_at: Timestamp } | null;
  /** PROPOSAL: for mail, the sender line and the severity the agent assigned. */
  thread: {
    from_text: string;
    severity: { level: 'high' | 'medium' | 'low'; text: string };
  } | null;
}

/** PROPOSAL: GET /v1/document-counts — the counts on the library tabs. */
interface DocumentCounts {
  all: number;
  regulation: number;
  internal: number;
  processing: number;
}

export interface LibraryQuery {
  category?: DocumentCategory;
  /** PROPOSAL: `processing` = pre-processing not finished. */
  state?: 'processing';
  collection_id?: string;
  /** PROPOSAL: documents whose validity relation waits for the person in charge (UC-05-05). */
  validity_review?: boolean;
}

/** PROPOSAL: GET /v1/documents (the spec only has POST and GET by id). */
export const listLibrary = (query: LibraryQuery = {}) =>
  v1Get<Page<DocumentRow>>('/documents', { ...ALL, ...query });

export const getDocumentCounts = () => v1Get<DocumentCounts>('/document-counts');

/* ─── Trình xem: one document ─────────────────────────────────────────── */

export type DocumentKind = 'file' | 'email' | 'call';
export type SignalTone = 'success' | 'warning' | 'error' | 'info' | 'neutral';

/** One field of the extracted record (extracted_record) with where it was read. */
export interface ExtractedField {
  name: string;
  value: string;
  page: number;
  confidence: number;
  /** below the schema's threshold: drawn outlined, waits for a person. */
  review: boolean;
}

/** PROPOSAL: the open quality issue of the document, drawn under the fields ("Sửa"). */
export interface FieldIssue {
  quality_issue_id: Uuid;
  field: string;
  title: string;
  detail: string;
  /** the value of the system of record; "Sửa thành …" writes it. */
  suggested_value: string;
  current_value: string;
}

export interface EmailMessage {
  id: Uuid;
  from: string;
  sent_at_text: string;
  body: string;
  attachments: string[];
}

/** PROPOSAL: a review the agent asked for inside the thread (a HitlRequest of kind review). */
export interface ThreadReview {
  hitl_id: Uuid;
  kind_label: string;
  from_text: string;
  title: string;
  detail: string;
}

export interface Fact {
  label: string;
  value: string;
  /** a fact drawn as a status dot or a badge rather than plain text. */
  tone?: SignalTone;
  badge?: boolean;
}

/** PROPOSAL: email_message rows of the document plus what the agent extracted. */
export interface EmailThread {
  subject: string;
  messages: EmailMessage[];
  facts: Fact[];
  similar: { title: string; detail: string } | null;
  review: ThreadReview | null;
}

export interface TranscriptLine {
  at_s: number;
  speaker: string;
  role: 'agent' | 'customer';
  text: string;
}

/** PROPOSAL: transcript_segment rows of a call recording plus what the agent extracted. */
export interface CallRecording {
  title: string;
  policy: { tone: SignalTone; text: string };
  duration_s: number;
  /** presigned audio; null while the recording is only available as text. */
  audio_url: string | null;
  /** bar heights 0..1 of the waveform, left to right. */
  waveform: number[];
  /** the stretch the agent took the commitment from, drawn in warning on the waveform. */
  highlight: { from_s: number; to_s: number } | null;
  transcript: TranscriptLine[];
  facts: Fact[];
  agent_note: string | null;
}

/**
 * GET /v1/documents/{id} as the viewers read it: the spec's state and pre-processing progress
 * (DocumentDetail) plus PROPOSAL fields for the viewer.
 */
export interface DocumentView extends DocumentDetail {
  kind: DocumentKind;
  file_name: string;
  /** null for a document outside any collection (an upload not yet assigned). */
  collection: { id: Uuid; name: string } | null;
  page_count: number;
  /** the page the viewer opens on (the one with an open issue, else 1). */
  focus_page: number;
  /** "Agent đã đọc hợp đồng này và trích 14 trường…" */
  summary_text: string | null;
  /** "Hợp đồng tín dụng" — the class the agent assigned. */
  type_label: string;
  /** "Phân loại 0,99", "2 trường cần xem", "Đã che CCCD" */
  signals: { tone: SignalTone; text: string }[];
  fields: ExtractedField[];
  issue: FieldIssue | null;
  /** "412 đoạn trong bộ sưu tập dùng tài liệu này · chỉ mục 06:00" */
  index_text: string | null;
  email: EmailThread | null;
  call: CallRecording | null;
}

export const getDocumentView = async (id: string) =>
  v1Get<DocumentView>(`/documents/${pathId(id)}`);

/** PROPOSAL: POST /v1/documents/{id}:investigate — Agent Phân tích looks for a systemic cause. */
export const investigateDocument = async (id: string) =>
  v1Send<{ run_id: Uuid }>('POST', `/documents/${pathId(id)}:investigate`);

/** A box on the page, in the page's own units (width × height of DocumentPage). */
export interface PageBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** GET /v1/documents/{id}/pages/{n}: "Trang: ảnh, văn bản, đoạn". PROPOSAL: the layout shape. */
export interface DocumentPage {
  number: number;
  width: number;
  height: number;
  /** presigned page image; null when only the layout is kept (then the layout is drawn). */
  image_url: string | null;
  /** title / heading / line boxes of the layout. */
  blocks: { kind: 'title' | 'heading' | 'line'; text: string; box: PageBox }[];
  highlights: { field: string; label: string; review: boolean; box: PageBox }[];
  /** the OCR text of the page (personal data already masked). */
  text: string;
}

export const getDocumentPage = async (id: string, n: number) =>
  v1Get<DocumentPage>(`/documents/${pathId(id)}/pages/${pathId(String(n))}`);

export interface DocumentChunk {
  id: Uuid;
  page: number;
  ordinal: number;
  text: string;
}

/** PROPOSAL: GET /v1/documents/{id}/chunks — the chunks of the document (tab Đoạn văn). */
export const listChunks = async (id: string) =>
  v1Get<Page<DocumentChunk>>(`/documents/${pathId(id)}/chunks`, ALL);

export interface DocumentLink {
  id: Uuid;
  /** "LD2409-0187", "Thông tư phân loại nợ" */
  target_label: string;
  /** codes are drawn in mono, titles in body. */
  target_is_code: boolean;
  kind_label: string;
  /** another document opens in the viewer. */
  target_document_id: Uuid | null;
  /** null = a plain reference (badge "Tham chiếu"). */
  state: { tone: SignalTone; text: string } | null;
}

/** PROPOSAL: GET /v1/documents/{id}/links — records and documents it is linked to (tab Liên kết). */
export const listLinks = async (id: string) =>
  v1Get<Page<DocumentLink>>(`/documents/${pathId(id)}/links`, ALL);

/** PROPOSAL: GET /v1/documents/{id}/activity — reads, re-extractions, ingestion (tab Lịch sử). */
export const listActivity = async (id: string) =>
  v1Get<Page<{ id: Uuid; when_text: string; text: string }>>(
    `/documents/${pathId(id)}/activity`,
    ALL,
  );

/* ─── Hàng chờ chất lượng (UC-05-03) ──────────────────────────────────── */

/** GET /v1/quality-issues. PROPOSAL: the fields of the side-by-side review. */
export interface QualityIssue {
  id: Uuid;
  document: { id: Uuid; title: string };
  page: number;
  /** "Agent Tài liệu không chắc kết quả OCR (độ tin 0,61) · có 1 đoạn nghi là dữ liệu cá nhân" */
  reason_text: string;
  /** presigned page image; null → the original lines are drawn. */
  image_url: string | null;
  original_lines: string[];
  /** what the agent read, personal data masked. */
  read: { heading: string | null; paragraphs: string[] };
  unreadable: string[];
  agent_actions: string[];
}

export type ResolveIssue =
  | { action: 'confirm' }
  | { action: 'edit'; text: string }
  | { action: 'drop'; reason: string }
  /** viewer: write the system-of-record value, or keep the read one and open a conflict. */
  | { action: 'correct'; value: string }
  | { action: 'keep_and_conflict' };

export const listQualityIssues = () =>
  v1Get<Page<QualityIssue>>('/quality-issues', { ...ALL, status: 'open' });

/** POST /v1/quality-issues/{id}:resolve. PROPOSAL: the body. */
export const resolveQualityIssue = async (id: string, body: ResolveIssue) =>
  v1Send<{ status: 'resolved' }>('POST', `/quality-issues/${pathId(id)}:resolve`, body);

/* ─── Hiệu lực văn bản (UC-05-05) ─────────────────────────────────────── */

/** GET /v1/documents/{id}/versions: "Dòng thời gian hiệu lực". PROPOSAL: this shape. */
export interface DocumentValidity {
  document_id: Uuid;
  title: string;
  status: { tone: SignalTone; text: string };
  /** "Người phụ trách: Ngô Thanh Bình (Pháp chế) · agent phát hiện văn bản thay thế ngày 02/09" */
  owner_text: string;
  timeline: { date_text: string; text: string; change: boolean }[];
  impact: { subject: string; effect: string }[];
  finding: { text: string; footnote: string } | null;
  /** the relation the agent proposed; null once confirmed. */
  pending: { superseded_by: Uuid; superseded_by_title: string; valid_to: string } | null;
  /** whether the caller is the person in charge, who alone can confirm (UC-05-05). */
  can_confirm: boolean;
}

export const getValidity = async (id: string) =>
  v1Get<DocumentValidity>(`/documents/${pathId(id)}/versions`);

/** PATCH /v1/documents/{id}: "Sửa số hiệu, hiệu lực, văn bản thay thế". */
export const setSupersession = async (
  id: string,
  body: { superseded_by: string; valid_to: string },
) => v1Send<DocumentValidity>('PATCH', `/documents/${pathId(id)}`, body);

/* ─── Trả lời từ tài liệu (UC-05-04) ──────────────────────────────────── */

export interface Citation {
  n: number;
  document_id: Uuid;
  title: string;
  /** "Điều 10, khoản 3 · trang 14" */
  locator: string;
  page: number;
  validity: { tone: SignalTone; text: string };
  /** a superseded source is only cited to say so, never as the basis. */
  superseded: boolean;
  quote: string;
}

/** PROPOSAL: POST /v1/document-answers — a question answered only from documents. */
export interface DocumentAnswer {
  id: Uuid;
  question: string;
  /** "Tìm trong: Văn bản pháp quy tín dụng · chỉ văn bản đang hiệu lực" */
  scope_text: string;
  /** the answer with [n] markers that refer to `citations`. */
  answer: string;
  /** nothing readable answers it: the answer says so and has no citations. */
  not_found: boolean;
  citations: Citation[];
  /** "Agent Tài liệu · 2 nguồn · 3,4 giây" */
  meta_text: string;
}

export const askDocuments = (body: { question: string; collection_id: string | null }) =>
  v1Send<DocumentAnswer>('POST', '/document-answers', body);

/* ─── Lược đồ trích xuất (UC-05-06) ───────────────────────────────────── */

export interface SchemaField {
  name: string;
  /** "text", "text · PII", "số", "danh mục", "bảng", "văn bản" */
  type_label: string;
  description: string;
  required: boolean;
  /** share right on the sample, 0..1; null before a test. */
  accuracy: number | null;
  /** ok, below the schema's threshold, or a field new in this version. */
  accuracy_state: 'ok' | 'low' | 'new';
  target_column: string;
}

/** PROPOSAL: GET /v1/extraction-schemas and /{id} (the spec has POST, :test, :activate). */
export interface ExtractionSchema {
  id: Uuid;
  collection: { id: Uuid; name: string };
  version: number;
  status: 'draft' | 'testing' | 'active' | 'superseded';
  /** "Agent Tài liệu đề xuất v2 từ 20 tệp mẫu: …" */
  proposal_text: string | null;
  fields: SchemaField[];
  output: {
    table: string;
    /** "18.400 dòng · cập nhật mỗi giờ · nguồn có cấu trúc cấp 2" */
    rows_text: string;
    used_by: string;
    sample_text: string | null;
    core_diff_text: string | null;
  };
  /** the sample size of :test. */
  test_size: number;
}

export const listSchemas = () => v1Get<Page<ExtractionSchema>>('/extraction-schemas', ALL);
export const getSchema = async (id: string) =>
  v1Get<ExtractionSchema>(`/extraction-schemas/${pathId(id)}`);

/** POST /v1/extraction-schemas/{id}:test — 202, the sample run goes on in the background. */
export const testSchema = async (id: string) =>
  v1Send<ExtractionSchema>('POST', `/extraction-schemas/${pathId(id)}:test`);

/** POST /v1/extraction-schemas/{id}:activate — publish this version. */
export const activateSchema = async (id: string) =>
  v1Send<ExtractionSchema>('POST', `/extraction-schemas/${pathId(id)}:activate`);

/* ─── Hộp thoại: upload, createCollection, suggestCollection ─────────── */

/** PROPOSAL: GET /v1/document-sources (the spec has POST): folders, mailboxes, call stores. */
export interface DocumentSource {
  id: Uuid;
  /** "SharePoint · /KiemTraSauVay" */
  label: string;
}

export const listDocumentSources = () => v1Get<Page<DocumentSource>>('/document-sources', ALL);

/** POST /v1/collections. PROPOSAL: the body. `schema_id` null = create after 20 samples. */
export interface NewCollection {
  name: string;
  file_types: string;
  source_id: string;
  schema_id: string | null;
  /** defaults to the caller (a collection the agent suggested). */
  owner_id?: string;
}

export const createCollection = (body: NewCollection) =>
  v1Send<{ id: Uuid; name: string }>('POST', '/collections', body);

export interface CollectionSuggestion {
  id: Uuid;
  /** "Biên bản kiểm tra sau cho vay · 2.140 tệp · SharePoint" */
  label: string;
  name: string;
  source_id: string;
}

/** PROPOSAL: POST /v1/collections:suggest — the agent scans connected file sources. */
export const suggestCollections = () =>
  v1Send<{ suggestions: CollectionSuggestion[]; note: string | null }>(
    'POST',
    '/collections:suggest',
  );

export type PiiPolicy = 'mask' | 'mask_and_review' | 'none';

/** PROPOSAL: POST /v1/documents:precheck — duplicates the agent will skip, before uploading. */
export const precheckUpload = (body: {
  collection_id: string;
  files: { file_name: string; size_bytes: number }[];
}) => v1Send<{ duplicates: number; note: string | null }>('POST', '/documents:precheck', body);

/**
 * POST /v1/documents (spec: multipart, 202). PROPOSAL: registered as JSON like /v1/uploads,
 * the bytes then go to the presigned URLs it returns; the mock skips that step.
 */
export const uploadDocuments = (body: {
  collection_id: string;
  schema_id: string | null;
  pii: PiiPolicy;
  files: { file_name: string; size_bytes: number }[];
}) => v1Send<{ accepted: number; skipped: number }>('POST', '/documents', body);
