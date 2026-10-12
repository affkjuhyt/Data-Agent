import { pathId, v1Events, v1Get, v1Send, type Page } from './client';

/*
 * The executive vault API (P16, UC-16-01…06, A2 "/v1/exec/*"). Everything that carries the
 * leader's intent arrives here as ciphertext (*_ct, wrapped_*) and is opened only on the device
 * (lib/vault); the server sees operational columns. Display-only joins beside the spec's fields
 * are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export type WrapKind = 'passkey' | 'hardware_key' | 'recovery_code';

/** A2: VaultKeyWrap. A paper code and a hardware key are offline; a passkey on a phone is not. */
export interface VaultKeyWrap {
  id: string;
  kind: WrapKind;
  credential_id: string | null;
  label: string;
  kdf: string;
  kdf_salt: string;
  wrapped_vmk: string;
  last_used_at: string | null;
  created_at: string;
}

export interface Vault {
  id: string;
  owner_id: string;
  status: 'active' | 'sealed' | 'shredded';
  box_public_key: string;
  box_private_key_ct: string;
  exit_policy: 'seal' | 'selective_handover' | 'shred';
  contract_ref: string | null;
  sealed_read_until: string | null;
  key_wraps: VaultKeyWrap[];
}

export const isOffline = (kind: WrapKind) => kind !== 'passkey';

export const getVault = async () => v1Get<Vault>('/exec/vault');

export const createVault = async (body: {
  box_public_key: string;
  box_private_key_ct: string;
  key_wraps: Omit<VaultKeyWrap, 'id' | 'last_used_at' | 'created_at'>[];
  exit_policy?: Vault['exit_policy'];
}) => v1Send<Vault & { transit_public_key: string }>('POST', '/exec/vault', body);

export const addKeyWrap = async (body: Omit<VaultKeyWrap, 'id' | 'last_used_at' | 'created_at'>) =>
  v1Send<VaultKeyWrap>('POST', '/exec/vault/key-wraps', body);

export const getTransitKey = async () =>
  v1Get<{ public_key_pem: string; key_version: number; algorithm: 'RSA-OAEP-SHA256' }>(
    '/exec/transit-key',
  );

/* ─── Theses ────────────────────────────────────────────────────────── */

export type ThesisStatus =
  | 'open'
  | 'scoring'
  | 'scored'
  | 'unresolvable'
  | 'withdrawn'
  | 'invalid_condition';

export interface Thesis {
  id: string;
  status: ThesisStatus;
  content_ct: string;
  wrapped_dek: string;
  content_version: number;
  cond_key_version: number;
  due_at: string;
  supersedes_id: string | null;
  committed_at: string;
  evidence_state: 'two_sided' | 'one_sided' | 'insufficient' | null;
  /** PROPOSAL: counts per side of the latest batch, operational and content-free. */
  evidence_counts: { support: number; oppose: number } | null;
  publication_id: string | null;
  withdrawn_at: string | null;
}

export const listTheses = async () => v1Get<Page<Thesis>>('/exec/theses', { limit: 500 });

export const createThesis = async (body: {
  id: string;
  content_ct: string;
  wrapped_dek: string;
  content_version: number;
  cond_ct: string;
  cond_key_version: number;
  due_at: string;
  supersedes_id?: string;
}) => v1Send<Thesis>('POST', '/exec/theses', body);

export const withdrawThesis = async (id: string) =>
  v1Send<Thesis>('POST', `/exec/theses/${pathId(id)}:withdraw`);

/* ─── Evidence, scores, access log ──────────────────────────────────── */

export interface EvidenceRow {
  id: string;
  batch_no: number;
  side: 'support' | 'oppose';
  item_kind: 'evidence' | 'none_found' | 'insufficient';
  strength: 'weak' | 'medium' | 'strong' | null;
  freshness_ok: boolean;
  /** crypto_box_seal to the vault's public key. */
  sealed_ct: string;
}

/** A2 returns the latest batch and the one before; the batch times are PROPOSAL. */
export interface EvidenceBatches {
  batches: { batch_no: number; ran_at: string; dropped_untraceable: number }[];
  items: EvidenceRow[];
}

export const getEvidence = async (thesisId: string) =>
  v1Get<EvidenceBatches>(`/exec/theses/${pathId(thesisId)}/evidence`);

export const refreshEvidence = async (thesisId: string) =>
  v1Send<{ agent_run_id: string }>('POST', `/exec/theses/${pathId(thesisId)}/evidence:refresh`);

export interface ThesisScore {
  id: string;
  thesis_id: string;
  kind: 'final' | 'restatement';
  outcome: 'correct' | 'incorrect' | 'unresolvable';
  scored_at: string;
  committed_at: string;
  /** Observed value, the condition used and the receipt, sealed to the vault. */
  sealed_ct: string;
}

export const listScores = async () => v1Get<Page<ThesisScore>>('/exec/scores');

export interface KeyAccessEntry {
  id: string;
  at: string;
  purpose: 'validate' | 'evidence_refresh' | 'scoring' | 'publish_validate' | 'unlock';
  workflow: string;
  /** "ExecThesisWorkflow · score_thesis", "Thiết bị · iPhone passkey" */
  actor_text: string;
  correlation_id: string | null;
  thesis_id: string | null;
}

export const listKeyAccess = async () => v1Get<Page<KeyAccessEntry>>('/exec/key-access-log');

/* ─── Memory ────────────────────────────────────────────────────────── */

export type MemoryKind = 'priority' | 'decision_reason' | 'question' | 'answer_snapshot' | 'note';

export interface MemoryItem {
  id: string;
  kind: MemoryKind;
  content_ct: string | null;
  wrapped_dek: string | null;
  content_version: number;
  related_thesis_id: string | null;
  created_at: string;
  shredded_at: string | null;
}

export const listMemoryItems = async () =>
  v1Get<Page<MemoryItem>>('/exec/memory-items', { limit: 5000 });

export const createMemoryItem = async (body: {
  id: string;
  kind: MemoryKind;
  content_ct: string;
  wrapped_dek: string;
  content_version: number;
  related_thesis_id?: string;
}) => v1Send<MemoryItem>('POST', '/exec/memory-items', body);

/** A2: 202 with the stream; the local model only, nothing logged (BR-193). */
export const askPrivately = async (body: {
  text: string;
  context_items: string[];
  as_of?: string;
}) => v1Send<{ id: string }>('POST', '/exec/private-questions', body);

export interface PrivateAnswer {
  text: string;
  /** Every number with the query run it came from: the layer below, never the vault. */
  figures: { value: string; query_run_id: string }[];
}

/** The answer of a private question, read whole from its stream; an `error` event throws. */
export async function privateAnswer(id: string): Promise<PrivateAnswer> {
  const events = await v1Events(`/exec/private-questions/${pathId(id)}/stream`);
  const failed = events.find((e) => e.event === 'error');
  if (failed) throw new Error(failed.data || 'Mô hình tại chỗ không trả lời được.');
  const answer = events.find((e) => e.event === 'answer');
  if (!answer) throw new Error('Luồng kết thúc mà không có câu trả lời.');
  return JSON.parse(answer.data) as PrivateAnswer;
}

/* ─── Publication and leaving ───────────────────────────────────────── */

interface PublicationRequest {
  title: string;
  statement_public: string;
  target_data_space_id: string;
  attribution: 'name' | 'title';
  attribution_label: string;
  confidence_public?: number;
  rationale_public?: string;
  ref_kind: 'metric' | 'data_product';
  metric_ref: string;
  scope?: Record<string, string>;
  period_label: string;
  comparator: string;
  threshold: number;
  threshold_hi?: number;
  unit: string;
}

export const publishThesis = async (thesisId: string, body: PublicationRequest) =>
  v1Send<{ id: string; status: string; reviewer_name: string }>(
    'POST',
    `/exec/theses/${pathId(thesisId)}/publications`,
    body,
  );

export const requestHandover = async (body: {
  kind: 'seal' | 'selective_handover' | 'shred';
  reason: string;
  successor_id?: string;
}) => v1Send<{ id: string; status: string; kind: string }>('POST', '/exec/handovers', body);

/**
 * PROPOSAL: Data Spaces a hypothesis can be published to, with who reviews there, and the title
 * the leader may sign with instead of the name ("Thành viên Ban điều hành").
 */
export const listPublishTargets = async () =>
  v1Get<{
    items: { id: string; name: string; reviewer_name: string }[];
    attribution_title: string;
  }>('/exec/publish-targets');
