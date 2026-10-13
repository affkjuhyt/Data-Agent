import { entropyToMnemonic } from '@scure/bip39';
import { wordlist } from '@scure/bip39/wordlists/english.js';
import { http, HttpResponse } from 'msw';
import {
  encryptItem,
  itemAad,
  newBoxPair,
  randomKey,
  ready,
  sealForBox,
  toB64,
} from '../lib/vault/crypto';
import { recoveryWrap } from '../lib/vault/session';
import type {
  EvidenceBatches,
  KeyAccessEntry,
  MemoryItem,
  Thesis,
  ThesisScore,
  Vault,
  VaultKeyWrap,
} from '../lib/v1/exec';

/*
 * The executive vault for the dev mock ("P16 · Lớp riêng lãnh đạo", 2425:15275, 2425:15639,
 * 2426:15422, 2426:15808). Like the real server it holds only ciphertext: the fixture is encrypted
 * with real keys at first use — a VMK wrapped by a paper code, the vault's box pair, every thesis
 * and memory item under its own DEK, evidence and scores sealed to the box key. The dev paper code
 * is DEV_RECOVERY_CODE below (fixed entropy, dev only); the passkey and hardware-key wraps of the
 * fixture are labels without a usable credential. Seeding costs one Argon2id run at the spec's
 * 256 MB, so it happens once per process and each handler set copies the result.
 */
export const DEV_RECOVERY_CODE = entropyToMnemonic(new Uint8Array(32).fill(7), wordlist);

const OWNER = 'u-exec-an';
const day = 86_400_000;
const at = (iso: string) => new Date(iso).toISOString();

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

interface ThesisPlain {
  id: string;
  status: Thesis['status'];
  due_at: string;
  committed_at: string;
  statement: string;
  rationale: string;
  confidence: number;
  condition: Record<string, unknown>;
  evidence_counts: Thesis['evidence_counts'];
  evidence_state: Thesis['evidence_state'];
  withdrawn_at?: string;
  outcome?: ThesisScore['outcome'];
  observed?: string;
  receipt?: string;
}

const cond = (
  label: string,
  ref: string,
  refKind: 'metric' | 'data_product',
  comparator: string,
  threshold: number,
  unit: string,
  period: string,
) => ({
  ref_kind: refKind,
  metric_ref: ref,
  metric_label: label,
  comparator,
  threshold,
  unit,
  period_label: period,
});

// Two open, the twelve scored of G10.5's worked example, one withdrawn, one unresolvable.
function plainTheses(): ThesisPlain[] {
  const scored = (
    id: string,
    statement: string,
    confidence: number,
    correct: boolean,
    due: string,
    observed: string,
    receipt: string,
  ): ThesisPlain => ({
    id,
    status: 'scored',
    due_at: at(due),
    committed_at: at(new Date(new Date(due).getTime() - 30 * day).toISOString()),
    statement,
    rationale: '',
    confidence,
    condition: cond(statement, 'metric', 'metric', 'gte', 0, '', ''),
    evidence_counts: null,
    evidence_state: null,
    outcome: correct ? 'correct' : 'incorrect',
    observed,
    receipt,
  });
  return [
    {
      id: '0192a1b2-0001-7000-8000-000000000001',
      status: 'open',
      due_at: at('2026-10-22T18:00:00+07:00'),
      committed_at: at('2026-10-06T14:12:00+07:00'),
      statement: 'Giá trần E5RON92 kỳ 22/10/2026 ≥ 20.000 đ/lít',
      rationale: 'Brent tăng 8% từ đầu tháng, Quỹ BOG đã chi bình ổn hai kỳ liền.',
      confidence: 0.7,
      condition: {
        ...cond(
          'Giá xăng dầu trong nước',
          'gia_xang_dau_trong_nuoc',
          'data_product',
          'gte',
          20000,
          'đ/lít',
          'điều hành 22/10/2026',
        ),
        scope_label: 'giá trần E5RON92',
      },
      evidence_counts: { support: 1, oppose: 2 },
      evidence_state: 'two_sided',
    },
    {
      id: '0192a1b2-0002-7000-8000-000000000002',
      status: 'open',
      due_at: at('2026-11-10T09:00:00+07:00'),
      committed_at: at('2026-10-02T10:00:00+07:00'),
      statement: 'Lãi suất huy động 12T bình quân thị trường tuần 45/2026 ≤ 5,0%',
      rationale: 'Thanh khoản hệ thống dồi dào, 6 tuần liền giảm.',
      confidence: 0.6,
      condition: {
        ...cond(
          'Lãi suất huy động 12 tháng bình quân',
          'lai_suat_huy_dong_12t_tb',
          'metric',
          'lte',
          5,
          '%',
          'tuần 45/2026',
        ),
        scope_label: 'toàn thị trường',
      },
      evidence_counts: { support: 2, oppose: 1 },
      evidence_state: 'two_sided',
    },
    scored(
      '0192a1b2-0003-7000-8000-000000000003',
      'Giá trần RON95-III kỳ 06/08/2026 ≤ 21.000 đ/lít',
      0.8,
      true,
      '2026-08-06T18:00:00+07:00',
      '20.870 đ/lít',
      'BN-20260806-000142',
    ),
    scored(
      '0192a1b2-0004-7000-8000-000000000004',
      'Tỷ lệ CASA T7/2026 ≥ 22%',
      0.85,
      false,
      '2026-08-05T09:00:00+07:00',
      '21,4%',
      'BN-20260805-000033',
    ),
    scored(
      '0192a1b2-0005-7000-8000-000000000005',
      'Nợ xấu bán lẻ T6/2026 ≤ 1,7%',
      0.55,
      false,
      '2026-07-10T09:00:00+07:00',
      '1,74%',
      'BN-20260710-000021',
    ),
    scored(
      '0192a1b2-0006-7000-8000-000000000006',
      'Dư nợ thẻ T6/2026 ≥ 9.500 tỷ',
      0.6,
      true,
      '2026-07-12T09:00:00+07:00',
      '9.612 tỷ',
      'BN-20260712-000044',
    ),
    scored(
      '0192a1b2-0007-7000-8000-000000000007',
      'Giá trần E5RON92 kỳ 02/07/2026 ≤ 20.000 đ/lít',
      0.65,
      false,
      '2026-07-02T18:00:00+07:00',
      '19.730 đ/lít',
      'BN-20260702-000190',
    ),
    scored(
      '0192a1b2-0008-7000-8000-000000000008',
      'Lãi suất huy động 12T tuần 30/2026 ≤ 5,2%',
      0.7,
      true,
      '2026-07-28T09:00:00+07:00',
      '5,05%',
      'BN-20260728-000071',
    ),
    scored(
      '0192a1b2-0009-7000-8000-000000000009',
      'Tăng trưởng tín dụng Q2/2026 ≥ 6%',
      0.75,
      true,
      '2026-07-05T09:00:00+07:00',
      '6,3%',
      'BN-20260705-000012',
    ),
    scored(
      '0192a1b2-0010-7000-8000-000000000010',
      'CIR Q2/2026 ≤ 33%',
      0.75,
      true,
      '2026-07-20T09:00:00+07:00',
      '32,4%',
      'BN-20260720-000088',
    ),
    scored(
      '0192a1b2-0011-7000-8000-000000000011',
      'Chi phí vốn T6/2026 ≤ 4,8%',
      0.78,
      false,
      '2026-07-08T09:00:00+07:00',
      '4,86%',
      'BN-20260708-000017',
    ),
    scored(
      '0192a1b2-0012-7000-8000-000000000012',
      'Giá trần DO kỳ 11/06/2026 ≤ 19.000 đ/lít',
      0.85,
      true,
      '2026-06-11T18:00:00+07:00',
      '18.640 đ/lít',
      'BN-20260611-000201',
    ),
    scored(
      '0192a1b2-0013-7000-8000-000000000013',
      'NIM Q2/2026 ≥ 3,4%',
      0.88,
      false,
      '2026-07-25T09:00:00+07:00',
      '3,32%',
      'BN-20260725-000064',
    ),
    scored(
      '0192a1b2-0015-7000-8000-000000000015',
      'Giá trần E5RON92 kỳ 21/05/2026 ≥ 19.000 đ/lít',
      0.92,
      true,
      '2026-05-21T18:00:00+07:00',
      '19.420 đ/lít',
      'BN-20260521-000155',
    ),
    {
      id: '0192a1b2-0016-7000-8000-000000000016',
      status: 'withdrawn',
      due_at: at('2026-09-05T09:00:00+07:00'),
      committed_at: at('2026-07-20T09:00:00+07:00'),
      statement: 'Tỷ lệ CASA T8/2026 ≥ 22%',
      rationale: '',
      confidence: 0.85,
      condition: cond('Tỷ lệ CASA', 'casa_ratio', 'metric', 'gte', 22, '%', 'T8/2026'),
      evidence_counts: null,
      evidence_state: null,
      withdrawn_at: at('2026-08-12T09:00:00+07:00'),
    },
    {
      id: '0192a1b2-0017-7000-8000-000000000017',
      status: 'unresolvable',
      due_at: at('2026-09-20T09:00:00+07:00'),
      committed_at: at('2026-08-20T09:00:00+07:00'),
      statement: 'Lãi suất liên ngân hàng qua đêm 20/09 ≤ 4%',
      rationale: '',
      confidence: 0.65,
      condition: cond(
        'Lãi suất liên ngân hàng qua đêm',
        'interbank_on',
        'metric',
        'lte',
        4,
        '%',
        '20/09/2026',
      ),
      evidence_counts: null,
      evidence_state: null,
    },
  ];
}

const memoryPlain: {
  id: string;
  kind: MemoryItem['kind'];
  text: string;
  tag: string;
  at: string;
}[] = [
  {
    id: '0192a1b2-1001-7000-8000-000000000101',
    kind: 'priority',
    text: 'Giữ chi phí vốn dưới 4,8%; không đua lãi suất 12 tháng với Ngân hàng B.',
    tag: 'Q4/2026 · ghi 02/10',
    at: '2026-10-02T08:00:00+07:00',
  },
  {
    id: '0192a1b2-1002-7000-8000-000000000102',
    kind: 'decision_reason',
    text: 'Chọn phương án B cho đợt huy động tháng 11 để giữ biên lãi ròng.',
    tag: 'từ Cần bạn · 05/10',
    at: '2026-10-05T16:00:00+07:00',
  },
  {
    id: '0192a1b2-1003-7000-8000-000000000103',
    kind: 'question',
    text: 'Lãi suất 12 tháng của mình đang cách bình quân thị trường bao nhiêu?',
    tag: '07/10 · kèm bản chụp câu trả lời',
    at: '2026-10-07T09:30:00+07:00',
  },
];

const evidenceItem = (
  key: string,
  sourceKind: string,
  title: string,
  text: string,
  queryRun: string | null,
  source: string,
) => ({ key, source_kind: sourceKind, title, text, query_run: queryRun, source });

interface Seed {
  vault: Vault;
  theses: Thesis[];
  memory: MemoryItem[];
  evidence: Record<string, EvidenceBatches>;
  scores: ThesisScore[];
  transitPem: string;
}

let seeded: Promise<Seed> | null = null;

async function seed(): Promise<Seed> {
  await ready;
  const vmk = randomKey();
  const pair = newBoxPair(vmk);
  const enc = (value: unknown, id: string, version = 1) =>
    encryptItem(vmk, value, itemAad(id, OWNER, version));
  const sealJson = (value: unknown) =>
    toB64(sealForBox(pair.publicKey, new TextEncoder().encode(JSON.stringify(value))));
  const now = new Date().toISOString();
  const wrapStub = (
    kind: VaultKeyWrap['kind'],
    label: string,
    lastUsed: string | null,
  ): VaultKeyWrap => ({
    id: `kw-${kind}`,
    kind,
    credential_id: null,
    label,
    kdf: 'hkdf_sha256',
    kdf_salt: toB64(randomKey()),
    wrapped_vmk: toB64(randomKey()),
    last_used_at: lastUsed,
    created_at: now,
  });
  const paper = recoveryWrap(vmk, DEV_RECOVERY_CODE, 'Mã khôi phục giấy 24 từ · 10/2026');
  const vault: Vault = {
    id: 'vault-an',
    owner_id: OWNER,
    status: 'active',
    box_public_key: toB64(pair.publicKey),
    box_private_key_ct: toB64(pair.privateCt),
    exit_policy: 'seal',
    contract_ref: 'HĐ-LĐ-2024-017',
    sealed_read_until: null,
    key_wraps: [
      wrapStub('passkey_prf', 'iPhone 15 · passkey', at('2026-10-10T08:12:00+07:00')),
      wrapStub('hardware_key', 'YubiKey 5C · két sắt', at('2026-09-02T10:00:00+07:00')),
      { ...paper, id: 'kw-paper', last_used_at: null, created_at: at('2026-03-03T09:00:00+07:00') },
    ],
  };
  const plain = plainTheses();
  const theses: Thesis[] = plain.map((t) => ({
    id: t.id,
    status: t.status,
    ...enc(
      {
        statement: t.statement,
        rationale: t.rationale,
        confidence: t.confidence,
        tags: [],
        condition: t.condition,
      },
      t.id,
    ),
    content_version: 1,
    cond_key_version: 1,
    due_at: t.due_at,
    supersedes_id: null,
    committed_at: t.committed_at,
    evidence_state: t.evidence_state,
    evidence_counts: t.evidence_counts,
    publication_id: null,
    withdrawn_at: t.withdrawn_at ?? null,
  }));
  const memory: MemoryItem[] = memoryPlain.map((m) => ({
    id: m.id,
    kind: m.kind,
    ...enc({ text: m.text, tag: m.tag }, m.id),
    content_version: 1,
    related_thesis_id: null,
    created_at: at(m.at),
    shredded_at: null,
  }));
  const scores: ThesisScore[] = plain
    .filter((t) => t.outcome)
    .map((t) => ({
      id: `sc-${t.id.slice(-4)}`,
      thesis_id: t.id,
      kind: 'final' as const,
      outcome: t.outcome!,
      scored_at: t.due_at,
      committed_at: t.committed_at,
      sealed_ct: sealJson({ observed: t.observed, receipt: t.receipt, condition_matches: true }),
    }));
  const e5 = plain[0].id;
  const row = (
    id: string,
    batch: number,
    side: 'support' | 'oppose',
    kind: 'evidence' | 'insufficient' | 'none_found',
    strength: 'weak' | 'medium' | 'strong' | null,
    payload: unknown,
  ) => ({
    id,
    batch_no: batch,
    side,
    item_kind: kind,
    strength,
    freshness_ok: kind !== 'insufficient',
    sealed_ct: sealJson(payload),
  });
  const bog = evidenceItem(
    'bog',
    'trang tri thức đã duyệt',
    'Quỹ bình ổn đã chi hai kỳ liền, dư Quỹ giảm',
    'Chi 300 đ/lít kỳ 01/10 và 08/10/2026 (minh hoạ)',
    'QR-20261008-0611',
    'trang chủ đề “Quỹ bình ổn giá xăng dầu” · kỳ 01/10–08/10/2026 · duyệt 08/10',
  );
  const stale = evidenceItem(
    'stale',
    'data product',
    'Bằng chứng không đủ: data product dừng ở kỳ 08/10/2026',
    'Nguồn quá hạn độ mới ghi là không đủ, không trình bày như bằng chứng (BR-190).',
    null,
    '',
  );
  const forecast = evidenceItem(
    'forecast',
    'dự báo đã phát hành',
    'Dự báo kỳ 22/10: xác suất đạt ngưỡng 0,31',
    'P(giá trần ≥ 20.000) = 0,31 · trung vị 19.760 đ/lít (minh hoạ)',
    'QR-20261009-0412',
    'formula_mc v2 đã duyệt · kỳ 22/10/2026 · phát hành 09/10 20:00',
  );
  const last = evidenceItem(
    'last',
    'data product đã phát hành',
    'Giá trần kỳ gần nhất thấp hơn ngưỡng',
    '19.640 đ/lít kỳ 08/10/2026, thấp hơn ngưỡng 360 đ/lít (minh hoạ)',
    'QR-20261008-0977',
    'Giá xăng dầu trong nước v3 · kỳ 08/10/2026 · nạp 08/10 15:20',
  );
  const evidence: Record<string, EvidenceBatches> = {
    [e5]: {
      batches: [
        { batch_no: 2, ran_at: at('2026-10-10T02:00:00+07:00'), dropped_untraceable: 0 },
        { batch_no: 1, ran_at: at('2026-10-09T02:00:00+07:00'), dropped_untraceable: 0 },
      ],
      items: [
        row('ev-21', 2, 'support', 'evidence', 'weak', bog),
        row('ev-22', 2, 'support', 'insufficient', null, stale),
        row('ev-23', 2, 'oppose', 'evidence', 'medium', forecast),
        row('ev-24', 2, 'oppose', 'evidence', 'weak', last),
        row('ev-11', 1, 'support', 'evidence', 'weak', bog),
        row('ev-12', 1, 'oppose', 'evidence', 'weak', last),
      ],
    },
  };
  // A throwaway RSA key stands in for the vault's Transit key (2048 bits here, 4096 in OpenBao).
  const rsa = await crypto.subtle.generateKey(
    {
      name: 'RSA-OAEP',
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: 'SHA-256',
    },
    true,
    ['encrypt', 'decrypt'],
  );
  const spki = new Uint8Array(await crypto.subtle.exportKey('spki', rsa.publicKey));
  const transitPem = `-----BEGIN PUBLIC KEY-----\n${toB64(spki)}\n-----END PUBLIC KEY-----`;
  if (typeof console !== 'undefined' && import.meta.env?.DEV && !import.meta.env?.VITEST)
    console.info(`[mock] Kho riêng dev: mã khôi phục giấy là “${DEV_RECOVERY_CODE}”`);
  return { vault, theses, memory, evidence, scores, transitPem };
}

// KeyAccessView as the api sends it; the screen's actor line is read from actor_kind and actor_ref.
const access = (
  id: string,
  iso: string,
  purpose: KeyAccessEntry['purpose'],
  action: string,
  actorKind: 'workflow' | 'owner_device',
  actorRef: string,
  correlationId: string,
  thesisId: string | null,
) => ({
  id,
  vault_id: 'vault-an',
  thesis_id: thesisId,
  key_kind: actorKind === 'workflow' ? 'condition_transit' : 'vault_key',
  action,
  purpose,
  actor_kind: actorKind,
  actor_ref: actorRef,
  correlation_id: correlationId,
  at: at(iso),
});
const T1 = '0192a1b2-0001-7000-8000-000000000001';
const accessLog = [
  access(
    'kal-4',
    '2026-10-22T18:00:04+07:00',
    'scoring',
    'decrypt_condition',
    'workflow',
    'ExecThesisWorkflow · score_thesis',
    '7f2a…c41e',
    T1,
  ),
  access(
    'kal-3',
    '2026-10-10T02:00:11+07:00',
    'evidence_refresh',
    'decrypt_condition',
    'workflow',
    'ExecThesisWorkflow · evidence_refresh',
    '19bd…07aa',
    T1,
  ),
  access(
    'kal-2',
    '2026-10-06T14:12:37+07:00',
    'validate',
    'decrypt_condition',
    'workflow',
    'ExecThesisWorkflow · validate_condition',
    'a3e0…5b19',
    T1,
  ),
  access(
    'kal-1',
    '2026-10-06T14:12:30+07:00',
    'unlock',
    'unlock',
    'owner_device',
    'iPhone passkey',
    '',
    null,
  ),
];

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

export function createExecHandlers() {
  let state: Seed | null = null;
  const load = async () => {
    if (!state) {
      seeded ??= seed();
      const s = await seeded;
      state = {
        ...s,
        vault: { ...s.vault, key_wraps: [...s.vault.key_wraps] },
        theses: [...s.theses],
        memory: [...s.memory],
        scores: [...s.scores],
      };
    }
    return state;
  };
  const wrapsOk = (wraps: { kind: string }[]) =>
    wraps.length >= 2 && wraps.some((w) => w.kind !== 'passkey_prf');
  return [
    http.get('*/v1/exec/vault', async () => HttpResponse.json((await load()).vault)),
    http.post('*/v1/exec/vault', async () =>
      problem(409, 'conflict', 'Bạn đã có kho riêng; mỗi người một kho.'),
    ),
    http.get('*/v1/exec/transit-key', async () =>
      HttpResponse.json({
        public_key_pem: (await load()).transitPem,
        key_version: 1,
        algorithm: 'RSA-OAEP-SHA256',
      }),
    ),
    http.post('*/v1/exec/vault/key-wraps', async ({ request }) => {
      const s = await load();
      const body = (await request.json()) as Omit<
        VaultKeyWrap,
        'id' | 'last_used_at' | 'created_at'
      >;
      const wrap: VaultKeyWrap = {
        ...body,
        id: `kw-${s.vault.key_wraps.length + 1}`,
        last_used_at: null,
        created_at: new Date().toISOString(),
      };
      s.vault = { ...s.vault, key_wraps: [...s.vault.key_wraps, wrap] };
      return HttpResponse.json(wrap, { status: 201 });
    }),
    http.get('*/v1/exec/theses', async () => page((await load()).theses)),
    http.post('*/v1/exec/theses', async ({ request }) => {
      const s = await load();
      const body = (await request.json()) as Thesis & { cond_ct: string };
      if (!wrapsOk(s.vault.key_wraps))
        return problem(422, 'business_rule_violated', 'Kho thiếu khoá bọc (BR-189).');
      if (new Date(body.due_at).getTime() - Date.now() < day)
        return problem(
          422,
          'business_rule_violated',
          'Ngày chấm phải sau hôm nay ít nhất 1 ngày (BR-188).',
        );
      if (!body.cond_ct) return problem(422, 'validation_failed', 'Thiếu điều kiện đã mã hoá.');
      const thesis: Thesis = {
        id: body.id,
        status: 'open',
        content_ct: body.content_ct,
        wrapped_dek: body.wrapped_dek,
        content_version: body.content_version,
        cond_key_version: body.cond_key_version,
        due_at: body.due_at,
        supersedes_id: body.supersedes_id ?? null,
        committed_at: new Date().toISOString(),
        evidence_state: null,
        evidence_counts: null,
        publication_id: null,
        withdrawn_at: null,
      };
      s.theses = [thesis, ...s.theses];
      return HttpResponse.json(thesis, { status: 201 });
    }),
    http.get('*/v1/exec/theses/:id/evidence', async ({ params }) => {
      const s = await load();
      const found = s.evidence[String(params.id)] ?? { batches: [], items: [] };
      // EvidenceView rows; `batches` (with what was dropped as untraceable) is the PROPOSAL extra.
      const ranAt = new Map(found.batches.map((b) => [b.batch_no, b.ran_at]));
      return HttpResponse.json({
        batches: found.batches,
        items: found.items.map((e) => ({
          ...e,
          thesis_id: String(params.id),
          agent_run_id: 'run-evidence',
          recorded_at: ranAt.get(e.batch_no) ?? '',
        })),
        next_page_token: '',
      });
    }),
    http.post('*/v1/exec/theses/:id/:action', async ({ params, request }) => {
      const s = await load();
      const t = s.theses.find((x) => x.id === params.id);
      if (!t) return problem(404, 'not_found', 'Không tìm thấy luận điểm.');
      if (params.action === 'evidence:refresh') {
        if (t.status !== 'open') return problem(409, 'invalid_state', 'Luận điểm không còn mở.');
        return HttpResponse.json(
          { id: 'run-evidence', status: 'queued', poll_after_ms: 500 },
          { status: 202 },
        );
      }
      if (params.action === 'publications') {
        const body = (await request.json()) as {
          statement_public: string;
          threshold: number;
          attribution: string;
        };
        if (body.attribution !== 'named' && body.attribution !== 'role_only')
          return problem(422, 'validation_failed', 'attribution: named hoặc role_only.');
        // A date may stay in the sentence; the threshold may not (it lives in the condition).
        const digits = body.statement_public.replace(/(\d)[.,](?=\d)/g, '$1');
        if (
          new RegExp(`(^|[^\\d/])${String(body.threshold).replace('.', '')}([^\\d/]|$)`).test(
            digits,
          )
        )
          return problem(
            422,
            'business_rule_violated',
            'Câu giả thuyết không chứa số; số nằm ở điều kiện có cấu trúc (BR-162).',
          );
        if (t.status === 'withdrawn' || t.status === 'unresolvable')
          return problem(409, 'invalid_state', 'Luận điểm đã rút hoặc không chấm được.');
        s.theses = s.theses.map((x) =>
          x.id === t.id ? { ...x, publication_id: `pub-${t.id.slice(-4)}` } : x,
        );
        return HttpResponse.json(
          { id: `pub-${t.id.slice(-4)}`, status: 'queued', poll_after_ms: 500 },
          { status: 202 },
        );
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.post('*/v1/exec/theses/:action', async ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const s = await load();
      const t = s.theses.find((x) => x.id === id);
      if (!t || verb !== 'withdraw') return problem(404, 'not_found', 'Không tìm thấy luận điểm.');
      if (t.status !== 'open') return problem(409, 'invalid_state', 'Đã tới hạn hoặc đã chấm.');
      const updated = {
        ...t,
        status: 'withdrawn' as const,
        withdrawn_at: new Date().toISOString(),
      };
      s.theses = s.theses.map((x) => (x.id === id ? updated : x));
      return HttpResponse.json(updated);
    }),
    http.get('*/v1/exec/scores', async () => page((await load()).scores)),
    http.get('*/v1/exec/key-access-log', () => page(accessLog)),
    http.get('*/v1/exec/memory-items', async () => page((await load()).memory)),
    http.post('*/v1/exec/memory-items', async ({ request }) => {
      const s = await load();
      const body = (await request.json()) as MemoryItem;
      const item: MemoryItem = {
        ...body,
        related_thesis_id: body.related_thesis_id ?? null,
        created_at: new Date().toISOString(),
        shredded_at: null,
      };
      s.memory = [item, ...s.memory];
      return HttpResponse.json(item, { status: 201 });
    }),
    http.post('*/v1/exec/private-questions', async ({ request }) => {
      // PrivateQuestionCreate: the items the device decrypted, as text, at most eight.
      const body = (await request.json()) as { context_items: { text?: unknown }[] };
      if (
        body.context_items.length > 8 ||
        body.context_items.some((c) => typeof c.text !== 'string' || !c.text)
      )
        return problem(422, 'validation_failed', 'context_items: tối đa 8 mục có text.');
      return HttpResponse.json(
        { id: 'pq-1', status: 'queued', poll_after_ms: 500 },
        { status: 202 },
      );
    }),
    http.get('*/v1/exec/private-questions/:id/stream', () => {
      const answer = {
        text: 'Lãi suất 12 tháng bình quân thị trường tuần 41 là 4,85%/năm; của mình 4,70%, thấp hơn 0,15 điểm.',
        figures: [{ value: '4,85%', query_run_id: 'QR-20261011-0101' }],
      };
      const body = `event: answer\ndata: ${JSON.stringify(answer)}\n\nevent: done\ndata: {}\n\n`;
      return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } });
    }),
    http.post('*/v1/exec/handovers', async ({ request }) => {
      const body = (await request.json()) as { kind: string; reason: string };
      if (!['leave_position', 'contract_end', 'owner_request'].includes(body.reason))
        return problem(422, 'validation_failed', 'Chọn lý do.');
      return HttpResponse.json(
        {
          id: 'ho-1',
          kind: body.kind,
          reason: body.reason,
          status: body.kind === 'seal' ? 'completed' : 'requested',
        },
        { status: 201 },
      );
    }),
  ];
}
