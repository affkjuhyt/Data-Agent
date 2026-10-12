/**
 * Kho riêng as the api sends it: revoked wraps do not open the vault, the evidence batches come
 * from the rows' batch numbers, the key-access line from actor_kind and actor_ref; the bodies
 * carry A3's values (named/role_only, a handover reason, the decrypted context items).
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  askPrivately,
  getEvidence,
  getVault,
  listKeyAccess,
  listTheses,
  requestHandover,
} from './exec';

const wrap = (id: string, kind: string, revoked_at: string | null) => ({
  id,
  vault_id: 'v-1',
  kind,
  credential_id: kind === 'recovery_code' ? null : 'cred',
  label: id,
  kdf: kind === 'recovery_code' ? 'argon2id' : 'hkdf_sha256',
  kdf_salt: 'c2FsdA==',
  wrapped_vmk: 'd3JhcA==',
  last_used_at: null,
  revoked_at,
  created_at: '2026-10-01T00:00:00Z',
});

const evidence = (id: string, batch_no: number, recorded_at: string) => ({
  id,
  thesis_id: 't-1',
  batch_no,
  side: 'support',
  item_kind: 'evidence',
  strength: 'strong',
  freshness_ok: true,
  sealed_ct: 'Y3Q=',
  agent_run_id: 'run-1',
  recorded_at,
});

const bodies: Record<string, unknown> = {};
const server = setupServer(
  http.get('*/v1/exec/vault', () =>
    HttpResponse.json({
      id: 'v-1',
      owner_id: 'u-1',
      status: 'active',
      box_public_key: 'cGs=',
      box_private_key_ct: 'c2s=',
      exit_policy: 'seal',
      contract_ref: null,
      sealed_read_until: null,
      key_wraps: [
        wrap('kw-paper', 'recovery_code', null),
        wrap('kw-phone', 'passkey_prf', '2026-10-05T00:00:00Z'),
        wrap('kw-yubi', 'hardware_key', null),
      ],
    }),
  ),
  http.get('*/v1/exec/theses', () =>
    HttpResponse.json({
      items: [
        {
          id: 't-1',
          vault_id: 'v-1',
          owner_id: 'u-1',
          content_ct: 'Y3Q=',
          wrapped_dek: 'ZGVr',
          content_version: 1,
          cond_key_version: 1,
          due_at: '2026-10-22T00:00:00Z',
          committed_at: '2026-10-06T00:00:00Z',
          status: 'open',
          evidence_state: 'two_sided',
          evidence_refreshed_at: null,
          supersedes_id: null,
          withdrawn_at: null,
          workflow_id: 'exec-thesis-t-1',
          created_at: '2026-10-06T00:00:00Z',
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/exec/theses/:id/evidence', () =>
    HttpResponse.json({
      items: [
        evidence('e-3', 2, '2026-10-10T02:00:05Z'),
        evidence('e-2', 2, '2026-10-10T02:00:09Z'),
        evidence('e-1', 1, '2026-10-03T02:00:00Z'),
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/exec/key-access-log', () =>
    HttpResponse.json({
      items: [
        {
          id: 'k-2',
          vault_id: 'v-1',
          thesis_id: 't-1',
          key_kind: 'condition_transit',
          action: 'decrypt_condition',
          purpose: 'scoring',
          actor_kind: 'workflow',
          actor_ref: 'ExecThesisWorkflow · score_thesis',
          correlation_id: 'corr-1',
          at: '2026-10-22T11:00:00Z',
        },
        {
          id: 'k-1',
          vault_id: 'v-1',
          thesis_id: null,
          key_kind: 'vault_key',
          action: 'unlock',
          purpose: 'unlock',
          actor_kind: 'owner_device',
          actor_ref: 'iPhone passkey',
          correlation_id: '',
          at: '2026-10-06T07:12:30Z',
        },
      ],
      next_page_token: '',
    }),
  ),
  http.post('*/v1/exec/private-questions', async ({ request }) => {
    bodies.question = await request.json();
    return HttpResponse.json({ id: 'pq-1', status: 'queued', poll_after_ms: 500 }, { status: 202 });
  }),
  http.post('*/v1/exec/handovers', async ({ request }) => {
    bodies.handover = await request.json();
    return HttpResponse.json({ id: 'ho-1', status: 'requested' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('the vault from the api', () => {
  it('opens only with the wraps that are not revoked', async () => {
    const vault = await getVault();
    expect(vault.key_wraps.map((w) => [w.id, w.kind, w.kdf])).toEqual([
      ['kw-paper', 'recovery_code', 'argon2id'],
      ['kw-yubi', 'hardware_key', 'hkdf_sha256'],
    ]);
  });

  it('reads a thesis without the PROPOSAL counts or publication', async () => {
    const [thesis] = (await listTheses()).items;
    expect(thesis).toMatchObject({
      id: 't-1',
      evidence_state: 'two_sided',
      evidence_counts: null,
      publication_id: null,
    });
  });

  it('groups the evidence by batch, newest first, timed by its latest row', async () => {
    const { batches, items } = await getEvidence('t-1');
    expect(batches).toEqual([
      { batch_no: 2, ran_at: '2026-10-10T02:00:09Z', dropped_untraceable: null },
      { batch_no: 1, ran_at: '2026-10-03T02:00:00Z', dropped_untraceable: null },
    ]);
    expect(items[0]).toEqual({
      id: 'e-3',
      batch_no: 2,
      side: 'support',
      item_kind: 'evidence',
      strength: 'strong',
      freshness_ok: true,
      sealed_ct: 'Y3Q=',
    });
  });

  it('writes who used the key from actor_kind and actor_ref', async () => {
    const [scoring, unlock] = (await listKeyAccess()).items;
    expect(scoring).toMatchObject({
      purpose: 'scoring',
      actor_text: 'ExecThesisWorkflow · score_thesis',
      correlation_id: 'corr-1',
    });
    expect(unlock).toMatchObject({ actor_text: 'Thiết bị · iPhone passkey', correlation_id: null });
  });

  it('sends the decrypted context and a handover reason as A3 has them', async () => {
    await askPrivately({
      text: 'Lãi suất 12 tháng?',
      context_items: [{ memory_item_id: 'm-1', text: 'Giữ chi phí vốn dưới 4,8%.' }],
    });
    expect(bodies.question).toEqual({
      text: 'Lãi suất 12 tháng?',
      context_items: [{ memory_item_id: 'm-1', text: 'Giữ chi phí vốn dưới 4,8%.' }],
    });
    await requestHandover({ kind: 'seal', reason: 'contract_end' });
    expect(bodies.handover).toEqual({ kind: 'seal', reason: 'contract_end' });
  });
});
