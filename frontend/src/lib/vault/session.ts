import type { Vault, VaultKeyWrap, WrapKind } from '../v1/exec';
import {
  decryptItem,
  encryptItem,
  fromB64,
  isRecoveryCode,
  itemAad,
  kdfString,
  newBoxPair,
  newSalt,
  openBoxPrivate,
  openSealed,
  parseKdf,
  randomKey,
  ready,
  RECOVERY_KDF,
  toB64,
  unwrapVmk,
  wipe,
  wrapKeyFromPrf,
  wrapKeyFromRecovery,
  wrapVmk,
} from './crypto';

/*
 * An open vault on this device (G10.1): the VMK and the box private key in memory only, for as
 * long as the vault is open; `lock()` overwrites them. Unlocking tries the leader's key wraps — a
 * passkey through WebAuthn PRF, or the paper code through Argon2id with the parameters the wrap
 * recorded — and a wrong key fails the AEAD instead of yielding garbage.
 */
export interface VaultSession {
  ownerId: string;
  boxPublicKey: Uint8Array;
  /** Which wrap opened the vault, for the banner ("iPhone · passkey"). */
  unlockedWith: { kind: WrapKind; label: string };
  decrypt<T>(contentCt: string, wrappedDek: string, id: string, contentVersion: number): T;
  encrypt(
    value: unknown,
    id: string,
    contentVersion: number,
  ): { content_ct: string; wrapped_dek: string };
  openSealed<T>(sealedCt: string): T;
  /** The VMK wrapped under another key — how a passkey is added to an open vault. */
  wrapVmkWith(wrapKey: Uint8Array): string;
  lock(): void;
}

function sessionFrom(vault: Vault, vmk: Uint8Array, wrap: VaultKeyWrap): VaultSession {
  const boxPublicKey = fromB64(vault.box_public_key);
  let boxPrivate: Uint8Array | null = openBoxPrivate(vmk, fromB64(vault.box_private_key_ct));
  let key: Uint8Array | null = vmk;
  const live = () => {
    if (!key || !boxPrivate) throw new Error('Kho đã khoá.');
    return { key, boxPrivate };
  };
  return {
    ownerId: vault.owner_id,
    boxPublicKey,
    unlockedWith: { kind: wrap.kind, label: wrap.label },
    decrypt: (ct, dek, id, version) =>
      decryptItem(live().key, ct, dek, itemAad(id, vault.owner_id, version)),
    encrypt: (value, id, version) =>
      encryptItem(live().key, value, itemAad(id, vault.owner_id, version)),
    openSealed: <T>(sealed: string) =>
      JSON.parse(
        new TextDecoder().decode(openSealed(boxPublicKey, live().boxPrivate, fromB64(sealed))),
      ) as T,
    wrapVmkWith: (wrapKey) => toB64(wrapVmk(wrapKey, live().key)),
    lock: () => {
      wipe(key);
      wipe(boxPrivate);
      key = null;
      boxPrivate = null;
    },
  };
}

class UnlockError extends Error {}

/** Try every paper-code wrap of the vault with the code typed in. */
export async function unlockWithRecoveryCode(vault: Vault, code: string): Promise<VaultSession> {
  await ready;
  if (!isRecoveryCode(code)) throw new UnlockError('Mã khôi phục không hợp lệ: cần đúng 24 từ.');
  for (const wrap of vault.key_wraps.filter((w) => w.kind === 'recovery_code')) {
    const wrapKey = wrapKeyFromRecovery(code, fromB64(wrap.kdf_salt), parseKdf(wrap.kdf));
    try {
      return sessionFrom(vault, unwrapVmk(wrapKey, fromB64(wrap.wrapped_vmk)), wrap);
    } catch {
      // Not this wrap; try the next.
    } finally {
      wipe(wrapKey);
    }
  }
  throw new UnlockError('Mã khôi phục không mở được kho này.');
}

/** WebAuthn get() with the PRF extension, one salt per wrap (G10.2 step 1). */
export async function unlockWithPasskey(vault: Vault): Promise<VaultSession> {
  await ready;
  const wraps = vault.key_wraps.filter((w) => w.kind !== 'recovery_code' && w.credential_id);
  if (wraps.length === 0 || !navigator.credentials)
    throw new UnlockError('Thiết bị này không có passkey của kho.');
  const assertion = (await navigator.credentials.get({
    publicKey: {
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      allowCredentials: wraps.map((w) => ({
        type: 'public-key' as const,
        id: fromB64(w.credential_id!) as BufferSource,
      })),
      userVerification: 'required',
      extensions: {
        prf: {
          evalByCredential: Object.fromEntries(
            wraps.map((w) => [w.credential_id!, { first: fromB64(w.kdf_salt) }]),
          ),
        },
      } as AuthenticationExtensionsClientInputs,
    },
  })) as PublicKeyCredential | null;
  if (!assertion) throw new UnlockError('Đã huỷ xác thực passkey.');
  const used = wraps.find((w) => toB64(new Uint8Array(assertion.rawId)) === w.credential_id);
  const prf = (
    assertion.getClientExtensionResults() as { prf?: { results?: { first?: ArrayBuffer } } }
  ).prf?.results?.first;
  if (!used || !prf) throw new UnlockError('Passkey này không hỗ trợ PRF; dùng mã khôi phục.');
  const wrapKey = await wrapKeyFromPrf(new Uint8Array(prf), fromB64(used.kdf_salt));
  try {
    return sessionFrom(vault, unwrapVmk(wrapKey, fromB64(used.wrapped_vmk)), used);
  } catch {
    throw new UnlockError('Passkey không mở được kho này.');
  } finally {
    wipe(wrapKey);
  }
}

/** A paper-code wrap of a VMK, at the spec's KDF (Argon2id m = 256 MB, t = 3). */
export function recoveryWrap(vmk: Uint8Array, code: string, label: string) {
  const salt = newSalt();
  const spec = { kind: 'argon2id' as const, ...RECOVERY_KDF };
  const wrapKey = wrapKeyFromRecovery(code, salt, spec);
  try {
    return {
      kind: 'recovery_code' as const,
      credential_id: null,
      label,
      kdf: kdfString(spec),
      kdf_salt: toB64(salt),
      wrapped_vmk: toB64(wrapVmk(wrapKey, vmk)),
    };
  } finally {
    wipe(wrapKey);
  }
}

/**
 * A new vault (UC-16-01 first use): VMK, the box pair, and two paper-code wraps, both offline.
 * A passkey can be added afterwards from the Khoá tab. The caller shows the codes, has each one
 * tried, then posts the result; the VMK is wiped here.
 */
export async function newVaultMaterial(codes: { code: string; label: string }[]) {
  await ready;
  const vmk = randomKey();
  try {
    const pair = newBoxPair(vmk);
    const material = {
      box_public_key: toB64(pair.publicKey),
      box_private_key_ct: toB64(pair.privateCt),
      key_wraps: codes.map((c) => recoveryWrap(vmk, c.code, c.label)),
    };
    wipe(pair.privateKey);
    return material;
  } finally {
    wipe(vmk);
  }
}

/**
 * Register a passkey on this device and wrap the open vault's VMK under its PRF output. The PRF
 * salt is fresh per wrap. Authenticators that do not answer PRF at creation are asked once more.
 */
export async function passkeyWrap(
  session: VaultSession,
  user: { id: string; name: string },
  label: string,
) {
  await ready;
  if (!navigator.credentials) throw new UnlockError('Trình duyệt này không hỗ trợ passkey.');
  const salt = newSalt();
  const created = (await navigator.credentials.create({
    publicKey: {
      rp: { name: 'Data Agent · Kho riêng' },
      user: { id: new TextEncoder().encode(user.id), name: user.name, displayName: user.name },
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      pubKeyCredParams: [
        { type: 'public-key', alg: -7 },
        { type: 'public-key', alg: -257 },
      ],
      authenticatorSelection: { residentKey: 'required', userVerification: 'required' },
      extensions: { prf: { eval: { first: salt } } } as AuthenticationExtensionsClientInputs,
    },
  })) as PublicKeyCredential | null;
  if (!created) throw new UnlockError('Đã huỷ tạo passkey.');
  const credentialId = toB64(new Uint8Array(created.rawId));
  type Prf = { prf?: { enabled?: boolean; results?: { first?: ArrayBuffer } } };
  let prf = (created.getClientExtensionResults() as Prf).prf?.results?.first;
  if (!prf) {
    const asserted = (await navigator.credentials.get({
      publicKey: {
        challenge: crypto.getRandomValues(new Uint8Array(32)),
        allowCredentials: [{ type: 'public-key', id: created.rawId }],
        userVerification: 'required',
        extensions: { prf: { eval: { first: salt } } } as AuthenticationExtensionsClientInputs,
      },
    })) as PublicKeyCredential | null;
    prf = (asserted?.getClientExtensionResults() as Prf | undefined)?.prf?.results?.first;
  }
  if (!prf)
    throw new UnlockError('Passkey này không hỗ trợ PRF; dùng khoá phần cứng hoặc mã giấy.');
  const wrapKey = await wrapKeyFromPrf(new Uint8Array(prf), salt);
  try {
    return {
      kind: 'passkey_prf' as const,
      credential_id: credentialId,
      label,
      kdf: kdfString({ kind: 'prf-hkdf' }),
      kdf_salt: toB64(salt),
      wrapped_vmk: session.wrapVmkWith(wrapKey),
    };
  } finally {
    wipe(wrapKey);
  }
}
