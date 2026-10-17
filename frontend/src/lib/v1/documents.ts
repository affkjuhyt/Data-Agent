import { pathId, v1Get, v1GetBlob, v1Send, v1Upload, type Page } from './client';
import type { components } from './schema';
import { listCollections, readCollectionDocument, type DocumentDetail, type Tone } from './sources';

type Schemas = components['schemas'];

/*
 * P05 Tài liệu phi cấu trúc beyond the collection list (which lives in sources.ts): the document
 * library, the document / email / call viewers, the quality queue, document validity, answers
 * from documents and extraction schemas. Paths follow "Đặc tả API", Document service:
 * /v1/documents, /v1/documents/{id}/pages/{n}, /v1/documents/{id}/versions, /v1/quality-issues,
 * /v1/extraction-schemas, /v1/collections. Everything the screens need beyond those rows is
 * marked PROPOSAL, as in types.ts, and served by mocks/documents.ts until the backend has it.
 * Where A2 serves the resource its shape is read below; the screen's richer row travels under
 * `display` when the mock sends it, and is said from the api's row otherwise.
 */

type Uuid = string;
type Timestamp = string;

const ALL = { page_size: 500 };

/* ─── Tệp & tài liệu: the document library ────────────────────────────── */

export type DocumentCategory = 'regulation' | 'internal' | 'contract' | 'correspondence';

/** A row of "v3.1 · Phi cấu trúc · Tệp & tài liệu" (GET /v1/documents, A2 since 12/10/2026). */
export interface DocumentRow {
  id: Uuid;
  title: string;
  /** PROPOSAL: the class the agent assigned; null from A2, which has none. */
  category: DocumentCategory | null;
  /** "Quy định", "Nội bộ"; from A2 the media kind ("PDF", "Word"). */
  category_label: string;
  /** "Từ 01/01/2027", "Đang hiệu lực"; `ended` draws "Hết hiệu lực 30/09" as a badge. */
  validity: { tone: Tone; text: string; ended: boolean } | null;
  /** PROPOSAL: clauses the agent extracted. */
  clause_count: number | null;
  /** PROPOSAL: "NPL_RATIO · đề xuất v5" — what the document is linked to. */
  links_text: string | null;
  /** Set while the pre-processing runs ("Đang xử lý" tab). */
  processing: { step_text: string; progress: number; started_at: Timestamp } | null;
  /** PROPOSAL: for mail, the sender line and the severity the agent assigned. */
  thread: {
    from_text: string;
    severity: { level: 'high' | 'medium' | 'low'; text: string };
  } | null;
  /** PROPOSAL: the validity relation the agent proposed waits for the person in charge. */
  validity_pending?: boolean;
}

/** The counts on the library tabs; a category count is absent when no row carries a category. */
export interface DocumentCounts {
  all: number;
  regulation?: number;
  internal?: number;
  processing: number;
}

export interface LibraryQuery {
  /** PROPOSAL: filtered here, on the rows that carry a category. */
  category?: DocumentCategory;
  /** `processing` = pre-processing not finished (A2 ?status=processing). */
  state?: 'processing';
  collection_id?: string;
  /** PROPOSAL: documents whose validity relation waits for the person in charge (UC-05-05). */
  validity_review?: boolean;
}

/** GET /v1/documents as A2 sends it; the screen's richer row travels under `display`. */
type LibraryWire = Schemas['DocumentListItem'] & { display?: DocumentRow };

const MEDIA_LABEL: Record<string, string> = {
  pdf: 'PDF',
  image: 'Ảnh',
  docx: 'Word',
  email: 'Thư điện tử',
  audio: 'Ghi âm',
  html: 'Trang web',
};

/** DocumentListItem.step in words (UC-05-02). */
const STEP_LABEL: Record<string, string> = {
  queued: 'Chờ xử lý',
  reading_pages: 'Đọc trang',
  chunking: 'Cắt đoạn',
  indexing: 'Lập chỉ mục',
  quality_review: 'Chờ người xem',
  done: 'Xong',
  failed: 'Lỗi',
  removed: 'Đã gỡ',
};

const ddmmyyyy = (iso: string) => iso.slice(0, 10).split('-').reverse().join('/');

/** How far the pre-processing got: pages read, then chunks indexed, as one share. */
function progressOf(p: Schemas['Progress']): number {
  const pages = p.pages_total ? p.pages_read / p.pages_total : 0;
  const chunks = p.chunks ? p.chunks_indexed / p.chunks : 0;
  return Math.min(1, pages / 2 + chunks / 2);
}

export function readLibraryRow(raw: LibraryWire): DocumentRow {
  if (raw.display) return raw.display;
  const today = new Date().toISOString().slice(0, 10);
  const ended = raw.effective_to !== null && raw.effective_to < today;
  const running = raw.status === 'received' || raw.status === 'processing';
  return {
    id: raw.id,
    title: raw.title ?? raw.file_name,
    category: null,
    category_label: MEDIA_LABEL[raw.media] ?? raw.media,
    validity: raw.effective_to
      ? {
          tone: ended ? 'idle' : 'success',
          text: `${ended ? 'Hết hiệu lực' : 'Hiệu lực tới'} ${ddmmyyyy(raw.effective_to)}`,
          ended,
        }
      : raw.effective_from
        ? { tone: 'success', text: `Từ ${ddmmyyyy(raw.effective_from)}`, ended: false }
        : null,
    clause_count: null,
    links_text: null,
    processing: running
      ? {
          step_text: STEP_LABEL[raw.step] ?? raw.step,
          progress: progressOf(raw.progress),
          started_at: raw.created_at,
        }
      : null,
    thread: null,
  };
}

/*
 * GET /v1/documents takes the collection and the status; the category and the validity review
 * are PROPOSAL and are filtered here on the rows that carry them.
 */
export const listLibrary = async (query: LibraryQuery = {}): Promise<Page<DocumentRow>> => {
  const page = await v1Get<Page<LibraryWire>>('/documents', {
    ...ALL,
    ...(query.collection_id ? { collection_id: query.collection_id } : {}),
    ...(query.state === 'processing' ? { status: 'processing' } : {}),
  });
  let items = page.items.map(readLibraryRow);
  if (query.category) items = items.filter((r) => r.category === query.category);
  if (query.validity_review) items = items.filter((r) => r.validity_pending);
  return { ...page, items };
};

/** The tab counts, from one read of the library (A1.4 pages carry no total). */
export const getDocumentCounts = async (): Promise<DocumentCounts> => {
  const { items } = await listLibrary();
  const categorised = items.some((r) => r.category !== null);
  const count = (c: DocumentCategory) =>
    categorised ? items.filter((r) => r.category === c).length : undefined;
  return {
    all: items.length,
    regulation: count('regulation'),
    internal: count('internal'),
    processing: items.filter((r) => r.processing).length,
  };
};

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
  original_url: string | null;
  email: EmailThread | null;
  call: CallRecording | null;
}

/** GET /v1/documents/{id} as the api sends it, with the viewer's row under `display`. */
type DocumentWire = Schemas['DocumentDetail'] & { original_url?: string | null; display?: DocumentView };

const KIND: Record<string, DocumentKind> = { email: 'email', audio: 'call' };

/** What the pre-processing says so far, as the viewer's signals. */
function signalsOf(raw: Schemas['DocumentDetail']): DocumentView['signals'] {
  const p = raw.progress;
  const signals: DocumentView['signals'] = [];
  if (raw.status === 'failed') signals.push({ tone: 'error', text: raw.error ?? 'Xử lý lỗi' });
  if (p.pages_total)
    signals.push({ tone: 'info', text: `Đọc ${p.pages_read}/${p.pages_total} trang` });
  if (raw.blocking_quality_issues > 0)
    signals.push({ tone: 'warning', text: `${raw.blocking_quality_issues} vấn đề chặn` });
  return signals;
}

/*
 * The viewer's PROPOSAL parts (the agent's summary, fields, thread, call) are absent without
 * `display`: the page shows the document's state, its pages and its index coverage.
 */
function readDocumentView(raw: DocumentWire, collections: Map<string, string>): DocumentView {
  if (raw.display) return raw.display;
  const p = raw.progress;
  return {
    ...readCollectionDocument(raw),
    kind: KIND[raw.media] ?? 'file',
    file_name: raw.file_name,
    collection: { id: raw.collection_id, name: collections.get(raw.collection_id) ?? '' },
    page_count: raw.page_count ?? 0,
    focus_page: 1,
    summary_text: null,
    type_label: raw.media.toUpperCase(),
    signals: signalsOf(raw),
    fields: [],
    issue: null,
    index_text: p.chunks ? `${p.chunks_indexed}/${p.chunks} đoạn đã lập chỉ mục` : null,
    original_url: raw.original_url ?? null,
    email: null,
    call: null,
  };
}

export const getDocumentView = async (id: string) => {
  const [raw, collections] = await Promise.all([
    v1Get<DocumentWire>(`/documents/${pathId(id)}`),
    listCollections().catch(() => null),
  ]);
  return readDocumentView(
    raw,
    new Map<string, string>(collections?.items.map((c) => [c.id, c.name]) ?? []),
  );
};

export const getDocumentOriginal = (id: string) =>
  v1GetBlob(`/documents/${pathId(id)}/file`);

/**
 * POST /v1/tasks (A2, UC-08-05): Agent Phân tích is given the document to look for a systemic
 * cause, as a background task; 202 names the task.
 */
export const investigateDocument = async (doc: { id: Uuid; title: string }) =>
  v1Send<Schemas['TaskAccepted']>('POST', '/tasks', {
    description: `Tìm nguyên nhân hệ thống của “${doc.title}” và các thư tương tự (tài liệu ${doc.id}).`,
  } satisfies Schemas['TaskCreate']);

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
  blocks: {
    kind: 'title' | 'heading' | 'paragraph' | 'picture' | 'line';
    text: string;
    box: PageBox;
    level?: number | null;
    image_url?: string | null;
  }[];
  highlights: { field: string; label: string; review: boolean; box: PageBox }[];
  /** the OCR text of the page (personal data already masked). */
  text: string;
}

type PageWire = Schemas['DocumentPageView'] & {
  width?: number | null;
  height?: number | null;
  blocks?: { kind: DocumentPage['blocks'][number]['kind']; text: string; box: number[]; level?: number | null; image_url?: string | null }[];
  display?: DocumentPage;
};

/** Without the PROPOSAL layout the page is its image and text on an A4 sheet (595 × 842 pt). */
export const getDocumentPage = async (id: string, n: number): Promise<DocumentPage> => {
  const raw = await v1Get<PageWire>(`/documents/${pathId(id)}/pages/${pathId(String(n))}`);
  return (
    raw.display ?? {
      number: raw.page_no,
      width: raw.width ?? 595,
      height: raw.height ?? 842,
      image_url: raw.image_url ?? null,
      blocks: (raw.blocks ?? []).map((block) => ({
        kind: block.kind,
        text: block.text,
        box: { x: block.box[0], y: block.box[1], w: block.box[2], h: block.box[3] },
        level: block.level,
        image_url: block.image_url,
      })),
      highlights: [],
      text: raw.text ?? '',
    }
  );
};

export interface DocumentChunk {
  id: Uuid;
  page: number;
  /** The chunk's place in the document, from 1. */
  ordinal: number;
  text: string;
}

/** Pages read for the Đoạn văn tab; a longer document shows the chunks of its first pages. */
/*
 * The chunks of the document (tab Đoạn văn): A2 serves them with each page
 * (GET /v1/documents/{id}/pages/{n}), so the pages are read in order and their chunks listed
 * once each (a chunk may span two pages).
 */
export const listChunks = async (doc: {
  id: Uuid;
  page_count: number;
}): Promise<Page<DocumentChunk>> => {
  const page = await v1Get<Page<Schemas['ChunkView']>>(
    `/documents/${pathId(doc.id)}/chunks`,
    { page_size: 500 },
  );
  return {
    next_page_token: '',
    items: page.items.map((c, i) => ({ id: c.id, page: c.page_from, ordinal: i + 1, text: c.text })),
  };
};

export interface DocumentActivity {
  id: Uuid;
  /** "08/10 10:42" */
  when_text: string;
  text: string;
}

const ddmmhhmm = (iso: string) => {
  const d = new Date(iso);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${two(d.getDate())}/${two(d.getMonth() + 1)} ${two(d.getHours())}:${two(d.getMinutes())}`;
};

/** audit_log.action of a document in words; another action is shown as recorded. */
const AUDIT_ACTION: Record<string, string> = {
  'document.read': 'xem tài liệu',
  'document.page.read': 'xem một trang',
  'document.update': 'sửa thông tin tài liệu',
  'document.upload': 'nạp tài liệu',
};

/*
 * Tab Lịch sử (SCR-34): the document's versions (GET /v1/documents/{id}/versions) as the
 * validity timeline, then who read or changed it from the audit log, which only a caller with
 * audit_read gets (403 otherwise, and the reads are left out). Newest first.
 */
export const listActivity = async (id: string): Promise<Page<DocumentActivity>> => {
  const [versions, audit] = await Promise.all([
    v1Get<Page<Schemas['DocumentView']>>(`/documents/${pathId(id)}/versions`, ALL),
    v1Get<Page<Schemas['AuditLogView']>>('/audit', {
      ...ALL,
      object_kind: 'document',
      object_id: id,
    }).catch(() => null),
  ]);
  const rows = [
    ...versions.items.map((v) => ({
      id: v.id,
      at: v.created_at,
      text: [
        `Nạp ${v.title ?? v.file_name}`,
        v.doc_no,
        v.effective_from && `hiệu lực từ ${ddmmyyyy(v.effective_from)}`,
      ]
        .filter(Boolean)
        .join(' · '),
    })),
    ...(audit?.items ?? []).map((a) => ({
      id: a.id,
      at: a.occurred_at,
      text: `${a.actor_kind === 'agent' ? 'Agent' : 'Người dùng'} ${AUDIT_ACTION[a.action] ?? a.action}`,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return {
    next_page_token: '',
    items: rows.map((r) => ({ id: r.id, when_text: ddmmhhmm(r.at), text: r.text })),
  };
};

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

type IssueWire = Schemas['QualityIssueView'] & { display?: QualityIssue };

/** quality_issue.kind in words. */
const ISSUE_KIND: Record<string, string> = {
  low_ocr: 'Nhận dạng chữ độ tin thấp',
  pii_unmasked: 'Có dữ liệu cá nhân chưa che',
  garbled_text: 'Chữ bị lỗi mã',
  empty_chunk: 'Đoạn rỗng',
  low_confidence_field: 'Trường trích độ tin thấp',
  field_mismatch: 'Trường lệch với hệ thống gốc',
};

/** `detail` is jsonb: a sentence, or an object whose text or message says it. */
const detailText = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (typeof detail === 'object' && detail !== null) {
    const { text, message } = detail as { text?: unknown; message?: unknown };
    if (typeof text === 'string') return text;
    if (typeof message === 'string') return message;
  }
  return '';
};

function readIssue(raw: IssueWire, titles: Map<string, string>): QualityIssue {
  if (raw.display) return raw.display;
  const detail = detailText(raw.detail);
  return {
    id: raw.id,
    document: { id: raw.document_id, title: titles.get(raw.document_id) ?? '' },
    page: raw.page_no ?? 1,
    reason_text: [ISSUE_KIND[raw.kind] ?? raw.kind, detail].filter(Boolean).join(' · '),
    image_url: null,
    original_lines: raw.old_value ? raw.old_value.split('\n') : [],
    read: { heading: null, paragraphs: detail ? [detail] : [] },
    unreadable: [],
    agent_actions: [],
  };
}

/** The open issues, each named by its document (A2 has no list of documents to join with). */
export const listQualityIssues = async (): Promise<Page<QualityIssue>> => {
  const page = await v1Get<Page<IssueWire>>('/quality-issues', { ...ALL, status: 'open' });
  const ids = [...new Set(page.items.filter((i) => !i.display).map((i) => i.document_id))];
  const docs = await Promise.all(
    ids.map((id) => v1Get<DocumentWire>(`/documents/${pathId(id)}`).catch(() => null)),
  );
  const titles = new Map<string, string>(
    docs.flatMap((d) => (d ? [[d.id, d.title ?? d.file_name] as [string, string]] : [])),
  );
  return { ...page, items: page.items.map((i) => readIssue(i, titles)) };
};

/*
 * The screen's five choices as A2's resolutions: confirm (the masking or the reading is right),
 * edit and correct (write the text or value), drop the page. Keeping the read value and opening
 * a conflict has no resolution in A2 (PROPOSAL).
 */
function resolutionOf(body: ResolveIssue): Schemas['ResolveIssue'] | null {
  switch (body.action) {
    case 'confirm':
      return { resolution: 'confirmed_mask' };
    case 'edit':
      return { resolution: 'corrected', corrected_text: body.text };
    case 'drop':
      return { resolution: 'page_dropped' };
    case 'correct':
      return { resolution: 'value_fixed', corrected_text: body.value };
    default:
      return null;
  }
}

/** POST /v1/quality-issues/{id}:resolve. */
export const resolveQualityIssue = async (id: string, body: ResolveIssue) => {
  const resolution = resolutionOf(body);
  // PROPOSAL: keep_and_conflict travels as the screen wrote it; the mock knows it.
  await v1Send('POST', `/quality-issues/${pathId(id)}:resolve`, resolution ?? body);
  return { status: 'resolved' as const };
};

/* ─── Hiệu lực văn bản (UC-05-05) ─────────────────────────────────────── */

/**
 * "Dòng thời gian hiệu lực" as the validity screen draws it. A2's versions give the timeline and
 * the status; the owner, the impact on answers, the agent's finding and its proposed relation are
 * PROPOSAL (empty without them), and only the person in charge confirms (UC-05-05).
 */
export interface DocumentValidity {
  document_id: Uuid;
  title: string;
  status: { tone: SignalTone; text: string };
  /** "Người phụ trách: Ngô Thanh Bình (Pháp chế) · agent phát hiện văn bản thay thế ngày 02/09" */
  owner_text: string | null;
  timeline: { date_text: string; text: string; change: boolean }[];
  impact: { subject: string; effect: string }[];
  finding: { text: string; footnote: string } | null;
  /** the relation the agent proposed; null once confirmed. */
  pending: { superseded_by: Uuid; superseded_by_title: string; valid_to: string } | null;
  /** whether the caller is the person in charge, who alone can confirm (UC-05-05). */
  can_confirm: boolean;
}

type VersionsWire = Page<Schemas['DocumentView']> & { display?: DocumentValidity };

/** The document's versions in time order, each one a step of the timeline. */
function readValidity(id: string, versions: Schemas['DocumentView'][]): DocumentValidity {
  const today = new Date().toISOString().slice(0, 10);
  const current = versions.find((v) => v.id === id) ?? versions[0];
  const name = (v: Schemas['DocumentView']) => v.title ?? v.file_name;
  const ended = current?.effective_to != null && current.effective_to < today;
  const steps = [...versions]
    .sort((a, b) =>
      (a.effective_from ?? a.created_at).localeCompare(b.effective_from ?? b.created_at),
    )
    .flatMap((v) => [
      {
        at: v.effective_from ?? v.created_at,
        text: [name(v), v.doc_no, v.effective_from && 'có hiệu lực'].filter(Boolean).join(' · '),
        change: v.id !== id,
      },
      ...(v.effective_to
        ? [{ at: v.effective_to, text: `${name(v)} hết hiệu lực`, change: v.id === id }]
        : []),
    ]);
  return {
    document_id: id,
    title: current ? name(current) : '',
    status: ended
      ? { tone: 'warning', text: 'Hết hiệu lực' }
      : { tone: 'success', text: 'Đang hiệu lực' },
    owner_text: null,
    timeline: steps.map((e) => ({ date_text: ddmmyyyy(e.at), text: e.text, change: e.change })),
    impact: [],
    finding: null,
    pending: null,
    can_confirm: false,
  };
}

export const getValidity = async (id: string): Promise<DocumentValidity> => {
  const raw = await v1Get<VersionsWire>(`/documents/${pathId(id)}/versions`, ALL);
  return raw.display ?? readValidity(id, raw.items);
};

/*
 * "Sửa quan hệ" as A2's PATCH /v1/documents/{id}: the replacing document records what it
 * supersedes, and the replaced one ends its validity the day before.
 */
export const setSupersession = async (
  id: string,
  body: { superseded_by: string; valid_to: string },
) => {
  await v1Send('PATCH', `/documents/${pathId(body.superseded_by)}`, {
    supersedes_id: id,
  } satisfies Schemas['DocumentPatch']);
  await v1Send('PATCH', `/documents/${pathId(id)}`, {
    effective_to: body.valid_to,
  } satisfies Schemas['DocumentPatch']);
};

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

/** A question answered from documents (UC-05-04), as the answer page draws it. */
export interface DocumentAnswer {
  id: Uuid;
  question: string;
  /** "Tìm trong: bộ sưu tập đã chọn" */
  scope_text: string;
  /** the answer, with [n] markers when the agent put them, that refer to `citations`. */
  answer: string;
  /** nothing readable answers it: the answer says so and has no citations. */
  not_found: boolean;
  citations: Citation[];
  /** "Agent Tài liệu · 2 nguồn · 3,4 giây" */
  meta_text: string;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Waits for the question's answer: GET /v1/questions/{id} names it once worker-agent is done. */
async function answerOf(questionId: string, pollMs: number): Promise<Schemas['AnswerView']> {
  for (let attempt = 0; attempt < 150; attempt++) {
    const q = await v1Get<Schemas['QuestionView']>(`/questions/${pathId(questionId)}`);
    if (q.answer_id) return v1Get<Schemas['AnswerView']>(`/answers/${pathId(q.answer_id)}`);
    if (q.status === 'failed' || q.status === 'cancelled')
      throw new Error('Câu hỏi không được trả lời.');
    await sleep(pollMs);
  }
  throw new Error('Chưa có câu trả lời, hãy thử lại sau.');
}

/** One entry of qa.answer.citations as worker-agent writes it (documents/answer.py). */
interface AnswerCitationWire {
  n: number;
  chunk_id: Uuid;
  document_id: Uuid;
  document_title: string | null;
  doc_no: string | null;
  section_path: string | null;
  page_from: number;
  effective_from: string | null;
  effective_to: string | null;
  superseded_by: string | null;
  quote: string;
}

function citationOf(c: AnswerCitationWire, today: string): Citation {
  const ended = c.effective_to !== null && c.effective_to < today;
  return {
    n: c.n,
    document_id: c.document_id,
    title: c.document_title ?? c.doc_no ?? 'Tài liệu',
    locator: [c.section_path, `trang ${c.page_from}`].filter(Boolean).join(' · '),
    page: c.page_from,
    validity: c.superseded_by
      ? { tone: 'warning', text: `Đã được thay thế bởi ${c.superseded_by}` }
      : ended
        ? { tone: 'warning', text: `Hết hiệu lực ${ddmmyyyy(c.effective_to!)}` }
        : c.effective_from && c.effective_from > today
          ? { tone: 'info', text: `Hiệu lực từ ${ddmmyyyy(c.effective_from)}` }
          : { tone: 'success', text: 'Đang hiệu lực' },
    superseded: Boolean(c.superseded_by) || ended,
    quote: c.quote,
  };
}

/*
 * A2 has no answer-from-documents resource: the question goes to Hỏi đáp (POST /v1/questions,
 * UC-05-04 is one of its use cases). worker-agent retrieves over every collection the reader may
 * read and writes the answer with its citations, each checked against the passage it names, so
 * the page shows exactly what the answer rests on. No purpose is declared: a purpose limits the
 * search to collections of that purpose (BR-59).
 */
export const askDocuments = async (body: { question: string }): Promise<DocumentAnswer> => {
  const started = Date.now();
  const accepted = await v1Send<Schemas['QuestionAccepted']>('POST', '/questions', {
    text: body.question,
    knowledge_mode: 'latest',
    stream: false,
  } satisfies Schemas['QuestionCreate']);
  const answer = await answerOf(accepted.question_id, accepted.poll_after_ms ?? 1000);
  const today = new Date().toISOString().slice(0, 10);
  const wire = (answer.kind === 'document' ? answer.citations : []) as AnswerCitationWire[] | null;
  const citations = (wire ?? []).map((c) => citationOf(c, today));
  const seconds = ((answer.latency_ms ?? Date.now() - started) / 1000).toFixed(1).replace('.', ',');
  return {
    id: answer.id,
    question: body.question,
    scope_text: 'Tìm trong: mọi bộ sưu tập bạn được đọc',
    answer: answer.text,
    not_found: answer.kind === 'refusal' || citations.length === 0,
    citations,
    meta_text: `${answer.agent_name ?? 'Agent Tài liệu'} · ${citations.length} nguồn · ${seconds} giây`,
  };
};

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

/** POST /v1/collections. Sources and extraction schemas are configured after creation. */
export interface NewCollection {
  name: string;
  file_types: string;
  purpose: string;
  pii_policy?: 'mask' | 'keep_for_authorized' | 'reject';
  retention_days?: number;
}

/** "Hợp đồng tín dụng" → "hop_dong_tin_dung" (document.collection.code). */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || 'collection';

type MediaKind = NonNullable<Schemas['CollectionCreate']['media_kinds']>[number];
const MEDIA: MediaKind[] = ['pdf', 'image', 'docx', 'email', 'audio', 'html'];

/** The kinds named in "PDF, ảnh, Word" as media kinds; none named takes every kind. */
const mediaOf = (fileTypes: string): MediaKind[] => {
  const text = fileTypes.toLowerCase();
  const words: Record<MediaKind, string[]> = {
    pdf: ['pdf'],
    image: ['ảnh', 'image', 'jpg', 'png', 'scan'],
    docx: ['word', 'docx', 'doc'],
    email: ['email', 'thư', 'mail'],
    audio: ['ghi âm', 'audio', 'cuộc gọi', 'mp3', 'wav'],
    html: ['html', 'web'],
  };
  return MEDIA.filter((m) => words[m].some((w) => text.includes(w)));
};

/**
 * POST /v1/collections (CollectionCreate). A source is bound separately through
 * POST /v1/document-sources, and an extraction schema is created after sample documents exist.
 */
export const createCollection = async (body: NewCollection) => {
  const media = mediaOf(body.file_types);
  const created = await v1Send<Schemas['CollectionView']>('POST', '/collections', {
    code: codeOf(body.name),
    name: body.name,
    purpose: body.purpose,
    ...(media.length ? { media_kinds: media } : {}),
    ...(body.pii_policy ? { pii_policy: body.pii_policy } : {}),
    ...(body.retention_days ? { retention_days: body.retention_days } : {}),
  } satisfies Schemas['CollectionCreate']);
  return { id: created.id, name: created.name };
};

export type PiiPolicy = 'mask' | 'mask_and_review' | 'none';

/**
 * POST /v1/documents: multipart, one file per request (A2). A file already in the collection
 * answers with that document instead of 202 — its status is not "received" — and counts as
 * skipped. The schema and the PII policy belong to the collection, not to the upload.
 */
export const uploadDocuments = async (body: {
  collection_id: string;
  schema_id: string | null;
  pii: PiiPolicy;
  files: File[];
}) => {
  const results = await Promise.all(
    body.files.map((file) => {
      const form = new FormData();
      form.append('collection_id', body.collection_id);
      form.append('file', file, file.name);
      return v1Upload<Schemas['services__api__modules__document__schemas__Accepted']>(
        '/documents',
        form,
      );
    }),
  );
  const accepted = results.filter((r) => r.status === 'received').length;
  return { accepted, skipped: results.length - accepted };
};
