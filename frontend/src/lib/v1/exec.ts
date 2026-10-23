import { pathId, v1Events, type Page, api, ok, okAs } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * The executive vault API (P16, UC-16-01…06, A2 "/v1/exec/*"). Everything that carries the
 * leader's intent arrives here as ciphertext (*_ct, wrapped_*) and is opened only on the device
 * (lib/vault); the server sees operational columns. Display-only joins beside the spec's fields
 * are PROPOSALs (docs/v3/05-ui-v3.md §4).
 */

export type WrapKind = Schemas['KeyWrapCreate']['kind'];

/** A2: VaultKeyWrap. A paper code and a hardware key are offline; a passkey on a phone is not. */
export interface VaultKeyWrap {
  id: string;
  kind: WrapKind;
  credential_id: string | null;
  label: string;
  /** hkdf_sha256 with PRF or hmac-secret, argon2id with the paper code; parameters are the spec's. */
  kdf: Schemas['KeyWrapCreate']['kdf'];
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

export const isOffline = (kind: WrapKind) => kind !== 'passkey_prf';

const readWrap = (raw: Schemas['KeyWrapView']): VaultKeyWrap => ({
  id: raw.id,
  kind: raw.kind as WrapKind,
  credential_id: raw.credential_id,
  label: raw.label,
  kdf: raw.kdf as VaultKeyWrap['kdf'],
  kdf_salt: raw.kdf_salt,
  wrapped_vmk: raw.wrapped_vmk,
  last_used_at: raw.last_used_at,
  created_at: raw.created_at,
});

/** A revoked wrap stays as a row (its history) but no longer opens the vault. */
export const getVault = async (): Promise<Vault> => {
  const raw = await ok<Schemas['VaultDetail']>(api.GET('/v1/exec/vault'));
  return {
    id: raw.id,
    owner_id: raw.owner_id,
    status: raw.status as Vault['status'],
    box_public_key: raw.box_public_key,
    box_private_key_ct: raw.box_private_key_ct,
    exit_policy: raw.exit_policy as Vault['exit_policy'],
    contract_ref: raw.contract_ref,
    sealed_read_until: raw.sealed_read_until,
    key_wraps: (raw.key_wraps ?? []).filter((w) => !w.revoked_at).map(readWrap),
  };
};

export const createVault = async (body: {
  box_public_key: string;
  box_private_key_ct: string;
  key_wraps: Omit<VaultKeyWrap, 'id' | 'last_used_at' | 'created_at'>[];
  exit_policy?: Vault['exit_policy'];
}) => ok<Schemas['VaultDetail']>(api.POST('/v1/exec/vault', { body }));

export const addKeyWrap = async (body: Omit<VaultKeyWrap, 'id' | 'last_used_at' | 'created_at'>) =>
  ok<Schemas['KeyWrapView']>(api.POST('/v1/exec/vault/key-wraps', { body }));

export const getTransitKey = async () =>
  ok<{ public_key_pem: string; key_version: number; algorithm: 'RSA-OAEP-SHA256' }>(
    api.GET('/v1/exec/transit-key'),
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
  /** PROPOSAL on the list: only GET /v1/exec/theses/{id} carries it. */
  publication_id: string | null;
  withdrawn_at: string | null;
}

type ThesisWire = (Schemas['ThesisView'] | Schemas['ThesisDetail']) &
  Partial<Pick<Thesis, 'evidence_counts' | 'publication_id'>>;

const readThesis = (raw: ThesisWire): Thesis => ({
  id: raw.id,
  status: raw.status as ThesisStatus,
  content_ct: raw.content_ct,
  wrapped_dek: raw.wrapped_dek,
  content_version: raw.content_version,
  cond_key_version: raw.cond_key_version,
  due_at: raw.due_at,
  supersedes_id: raw.supersedes_id,
  committed_at: raw.committed_at,
  evidence_state: (raw.evidence_state || null) as Thesis['evidence_state'],
  evidence_counts: raw.evidence_counts ?? null,
  publication_id: raw.publication_id ?? null,
  withdrawn_at: raw.withdrawn_at,
});

export const listTheses = async (): Promise<Page<Thesis>> => {
  const page = await ok<Page<ThesisWire>>(
    api.GET('/v1/exec/theses', { params: { query: { page_size: 500 } } }),
  );
  return { ...page, items: page.items.map(readThesis) };
};

/**
 * The content's AAD is id ‖ owner_id ‖ content_version (tab 3b), so the device makes the id
 * before it encrypts and sends it: A3's ThesisCreate takes it (a UUIDv7, since 13/10/2026).
 */
export const createThesis = async (body: {
  id: string;
  content_ct: string;
  wrapped_dek: string;
  content_version: number;
  cond_ct: string;
  cond_key_version: number;
  due_at: string;
  supersedes_id?: string;
}) => ok<Schemas['ThesisView']>(api.POST('/v1/exec/theses', { body }));

export const withdrawThesis = async (id: string) =>
  ok<Schemas['ThesisView']>(
    api.POST('/v1/exec/theses/{id}:withdraw', { params: { path: { id } } }),
  );

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

/**
 * A2 returns the latest batch and the one before, newest first. A batch's time is its latest
 * item's recorded_at; PROPOSAL: how many items the agent dropped as untraceable (null here).
 */
export interface EvidenceBatches {
  batches: { batch_no: number; ran_at: string; dropped_untraceable: number | null }[];
  items: EvidenceRow[];
}

type EvidenceWire = Schemas['EvidenceView'] & { display?: never };

export const getEvidence = async (thesisId: string): Promise<EvidenceBatches> => {
  const page = await ok<Page<EvidenceWire> & { batches?: EvidenceBatches['batches'] }>(
    api.GET('/v1/exec/theses/{id}/evidence', {
      params: { path: { id: thesisId }, query: { page_size: 500 } },
    }),
  );
  const ranAt = new Map<number, string>();
  for (const e of page.items) {
    const seen = ranAt.get(e.batch_no);
    if (!seen || e.recorded_at > seen) ranAt.set(e.batch_no, e.recorded_at);
  }
  return {
    batches:
      page.batches ??
      [...ranAt.entries()]
        .sort(([a], [b]) => b - a)
        .map(([batch_no, ran_at]) => ({ batch_no, ran_at, dropped_untraceable: null })),
    items: page.items.map((e) => ({
      id: e.id,
      batch_no: e.batch_no,
      side: e.side as EvidenceRow['side'],
      item_kind: e.item_kind as EvidenceRow['item_kind'],
      strength: e.strength as EvidenceRow['strength'],
      freshness_ok: e.freshness_ok,
      sealed_ct: e.sealed_ct,
    })),
  };
};

/** A2: 202 — the agent run that gathers the next batch. */
export const refreshEvidence = async (thesisId: string) =>
  ok<Schemas['services__api__modules__exec__schemas__Accepted']>(
    api.POST('/v1/exec/theses/{id}/evidence:refresh', { params: { path: { id: thesisId } } }),
  );

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

export const listScores = async () =>
  okAs<Page<ThesisScore>>(api.GET('/v1/exec/scores', { params: { query: { page_size: 500 } } }));

export interface KeyAccessEntry {
  id: string;
  at: string;
  purpose:
    | 'validate'
    | 'evidence_refresh'
    | 'scoring'
    | 'unlock'
    | 'handover'
    | 'rotation'
    | 'shred';
  /** "ExecThesisWorkflow · score_thesis", "Thiết bị · iPhone passkey" */
  actor_text: string;
  correlation_id: string | null;
  thesis_id: string | null;
}

/** actor_ref is the workflow and activity, the wrap's label, or the handover (tab 3b). */
const ACTOR: Record<string, string> = {
  owner_device: 'Thiết bị',
  admin_request: 'Yêu cầu quản trị',
};

export const listKeyAccess = async (): Promise<Page<KeyAccessEntry>> => {
  const page = await ok<Page<Schemas['KeyAccessView'] & { actor_text?: string }>>(
    api.GET('/v1/exec/key-access-log', { params: { query: { page_size: 500 } } }),
  );
  return {
    ...page,
    items: page.items.map((e) => ({
      id: e.id,
      at: e.at,
      purpose: e.purpose as KeyAccessEntry['purpose'],
      actor_text:
        e.actor_text ??
        (ACTOR[e.actor_kind] ? `${ACTOR[e.actor_kind]} · ${e.actor_ref}` : e.actor_ref),
      correlation_id: e.correlation_id || null,
      thesis_id: e.thesis_id,
    })),
  };
};

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
  okAs<Page<MemoryItem>>(
    api.GET('/v1/exec/memory-items', { params: { query: { page_size: 500 } } }),
  );

/** The same AAD as a thesis: the device's `id` (UUIDv7) in the body, as A3's MemoryItemCreate takes it. */
export const createMemoryItem = async (body: {
  id: string;
  kind: MemoryKind;
  content_ct: string;
  wrapped_dek: string;
  content_version: number;
  related_thesis_id?: string;
}) => okAs<MemoryItem>(api.POST('/v1/exec/memory-items', { body }));

/**
 * A2: 202 with the stream; the local model only, nothing logged (BR-193). The model cannot open
 * the vault, so the device sends the items it decrypted and chose to attach, at most eight.
 */
export const PRIVATE_CONTEXT_MAX = 8;

export const askPrivately = async (body: {
  text: string;
  context_items: { memory_item_id: string; text: string }[];
  as_of?: string;
}) =>
  ok<Schemas['services__api__modules__exec__schemas__Accepted']>(
    api.POST('/v1/exec/private-questions', { body }),
  );

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
  /** The Data Space the request is sent in (X-Data-Space); the api refuses any other. */
  target_data_space_id: string;
  /** named: signed with the name; role_only: with the title only. */
  attribution: 'named' | 'role_only';
  attribution_label: string;
  confidence_public?: number;
  rationale_public?: string;
  ref_kind: 'metric' | 'data_product';
  metric_ref: string;
  scope?: Record<string, string>;
  period_label: string;
  comparator: Schemas['PublicationCreate']['comparator'];
  threshold: number;
  threshold_hi?: number;
  unit: string;
}

/** A2: 202 — the publication waits for the reviewer of the target Data Space. */
export const publishThesis = async (thesisId: string, body: PublicationRequest) =>
  ok<Schemas['services__api__modules__exec__schemas__Accepted']>(
    api.POST('/v1/exec/theses/{id}/publications', { params: { path: { id: thesisId } }, body }),
  );

export type HandoverReason = Exclude<Schemas['HandoverCreate']['reason'], 'sealed_expired'>;

/** sealed_expired is the system's, when the sealed read window ends. */
export const requestHandover = async (body: {
  kind: 'seal' | 'selective_handover' | 'shred';
  reason: HandoverReason;
  successor_id?: string;
}) => ok<Schemas['HandoverView']>(api.POST('/v1/exec/handovers', { body }));

/**
 * Data Spaces a hypothesis can be published to: the viewer's own (GET /v1/data-spaces). The
 * reviewer is the target's steward; DataSpaceSummary carries only the steward's id, so no name.
 * The title the leader signs with instead of the name is typed in (attribution_label, A3).
 */
export interface PublishTarget {
  id: string;
  name: string;
  steward_id: string;
}

export const listPublishTargets = async (): Promise<PublishTarget[]> => {
  const page = await ok<Page<Schemas['DataSpaceSummary']>>(
    api.GET('/v1/data-spaces', { params: { query: { page_size: 500 } } }),
  );
  return page.items
    .filter((s) => s.status === 'active')
    .map((s) => ({ id: s.id, name: s.name, steward_id: s.steward_id }));
};
