import sodiumModule from 'libsodium-wrappers-sumo';
import { generateMnemonic, mnemonicToEntropy, validateMnemonic } from '@scure/bip39';
import { wordlist } from '@scure/bip39/wordlists/english.js';

/*
 * The device side of the executive vault (P16, tab 5 G10.1–G10.2, BR-189). The server never sees
 * plaintext or the vault key: everything carrying the leader's intent is sealed here.
 *
 * - VMK (32 bytes) lives only in memory while the vault is open. It is wrapped once per key wrap:
 *   a passkey (WebAuthn PRF output → HKDF-SHA256) or a 24-word paper code (Argon2id, m = 256 MB,
 *   t = 3). A wrap is XChaCha20-Poly1305(wrapKey, VMK).
 * - Each item has its own DEK: content_ct = nonce ‖ XChaCha20-Poly1305(DEK, content, AAD) with
 *   AAD = id ‖ owner_id ‖ content_version; wrapped_dek = XChaCha20-Poly1305(VMK, DEK).
 * - The vault's X25519 pair: the server keeps the public key so workflows can seal evidence and
 *   scores (crypto_box_seal); the private key is stored wrapped by the VMK and opens them here.
 * - The scoring condition alone is encrypted to the vault's Transit key (RSA-OAEP-SHA256), the one
 *   thing a workflow may read.
 * Binary values travel as standard base64.
 */
const sodium = sodiumModule;
export const ready = sodium.ready;

const B64 = () => sodium.base64_variants.ORIGINAL;
const toB64 = (bytes: Uint8Array) => sodium.to_base64(bytes, B64());
const fromB64 = (text: string) => sodium.from_base64(text, B64());
/*
 * libsodium checks `instanceof Uint8Array` against its own realm; bytes from TextEncoder (and from
 * jsdom in tests) can come from another one and are refused as "unsupported input type". Every
 * input crosses this copy first.
 */
const own = (bytes: Uint8Array) =>
  bytes.constructor === Uint8Array ? bytes : new Uint8Array(bytes);
const utf8 = (text: string) => own(new Uint8Array(sodium.from_string(text)));

const NONCE_BYTES = 24;
const KEY_BYTES = 32;

/** The paper code's KDF as the spec fixes it; a wrap records what it used in `kdf`. */
export const RECOVERY_KDF = { opsLimit: 3, memLimit: 256 * 1024 * 1024 } as const;

export function randomKey(): Uint8Array {
  return sodium.randombytes_buf(KEY_BYTES);
}

/** nonce ‖ XChaCha20-Poly1305(key, plaintext, aad) */
function seal(key: Uint8Array, plaintext: Uint8Array, aad: string): Uint8Array {
  const nonce = sodium.randombytes_buf(NONCE_BYTES);
  const ct = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
    own(plaintext),
    utf8(aad),
    null,
    nonce,
    own(key),
  );
  const out = new Uint8Array(NONCE_BYTES + ct.length);
  out.set(nonce);
  out.set(ct, NONCE_BYTES);
  return out;
}

/** Throws when the key or the AAD is wrong: a wrong key never yields garbage. */
function open(key: Uint8Array, sealed: Uint8Array, aad: string): Uint8Array {
  return sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
    null,
    own(sealed.subarray(NONCE_BYTES)),
    utf8(aad),
    own(sealed.subarray(0, NONCE_BYTES)),
    own(key),
  );
}

/** Overwrite secret bytes before letting them go. */
function wipe(bytes: Uint8Array | null | undefined) {
  if (bytes) sodium.memzero(bytes);
}

/* ─── Key wraps ─────────────────────────────────────────────────────── */

interface KdfSpec {
  kind: 'argon2id' | 'prf-hkdf';
  opsLimit?: number;
  memLimit?: number;
}

/** "argon2id;t=3;m=268435456" or "prf-hkdf-sha256" — what VaultKeyWrap.kdf stores. */
function kdfString(spec: KdfSpec): string {
  return spec.kind === 'argon2id'
    ? `argon2id;t=${spec.opsLimit};m=${spec.memLimit}`
    : 'prf-hkdf-sha256';
}

export function parseKdf(text: string): KdfSpec {
  if (text === 'prf-hkdf-sha256') return { kind: 'prf-hkdf' };
  const m = /^argon2id;t=(\d+);m=(\d+)$/.exec(text);
  if (!m) throw new Error(`KDF không hỗ trợ: ${text}`);
  return { kind: 'argon2id', opsLimit: Number(m[1]), memLimit: Number(m[2]) };
}

/** 24 words, 256 bits of entropy (BIP-39 English list). */
export function newRecoveryCode(): string {
  return generateMnemonic(wordlist, 256);
}

function normalizeRecoveryCode(text: string): string {
  return text.trim().toLowerCase().split(/\s+/).join(' ');
}

export function isRecoveryCode(text: string): boolean {
  return validateMnemonic(normalizeRecoveryCode(text), wordlist);
}

/** Argon2id over the code's entropy (not its words), so spacing and case never change the key. */
export function wrapKeyFromRecovery(code: string, salt: Uint8Array, spec: KdfSpec): Uint8Array {
  const entropy = mnemonicToEntropy(normalizeRecoveryCode(code), wordlist);
  return sodium.crypto_pwhash(
    KEY_BYTES,
    own(entropy),
    own(salt),
    spec.opsLimit ?? RECOVERY_KDF.opsLimit,
    spec.memLimit ?? RECOVERY_KDF.memLimit,
    sodium.crypto_pwhash_ALG_ARGON2ID13,
  );
}

/** HKDF-SHA256 over a passkey's PRF output (G10.2 step 1). */
export async function wrapKeyFromPrf(prfOutput: Uint8Array, salt: Uint8Array): Promise<Uint8Array> {
  const base = await crypto.subtle.importKey('raw', prfOutput as BufferSource, 'HKDF', false, [
    'deriveBits',
  ]);
  const bits = await crypto.subtle.deriveBits(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: salt as BufferSource,
      info: utf8('da-exec-vault-wrap-v1') as BufferSource,
    },
    base,
    KEY_BYTES * 8,
  );
  return new Uint8Array(bits);
}

export function newSalt(): Uint8Array {
  return sodium.randombytes_buf(sodium.crypto_pwhash_SALTBYTES);
}

const WRAP_AAD = 'da-exec-vmk';
export const wrapVmk = (wrapKey: Uint8Array, vmk: Uint8Array) => seal(wrapKey, vmk, WRAP_AAD);
export const unwrapVmk = (wrapKey: Uint8Array, wrapped: Uint8Array) =>
  open(wrapKey, wrapped, WRAP_AAD);

/* ─── The vault's box pair ──────────────────────────────────────────── */

const BOX_AAD = 'da-exec-box-sk';

export function newBoxPair(vmk: Uint8Array) {
  const pair = sodium.crypto_box_keypair();
  const privateCt = seal(vmk, pair.privateKey, BOX_AAD);
  return { publicKey: pair.publicKey, privateKey: pair.privateKey, privateCt };
}

export const openBoxPrivate = (vmk: Uint8Array, privateCt: Uint8Array) =>
  open(vmk, privateCt, BOX_AAD);

/** Seal for the vault (what workflows and a predecessor's handover do). */
export const sealForBox = (publicKey: Uint8Array, plaintext: Uint8Array) =>
  sodium.crypto_box_seal(own(plaintext), own(publicKey));

export const openSealed = (publicKey: Uint8Array, privateKey: Uint8Array, sealed: Uint8Array) =>
  sodium.crypto_box_seal_open(own(sealed), own(publicKey), own(privateKey));

/* ─── Items ─────────────────────────────────────────────────────────── */

export const itemAad = (id: string, ownerId: string, contentVersion: number) =>
  `${id}|${ownerId}|${contentVersion}`;

/** Encrypt one item's JSON with a fresh DEK; returns the two base64 fields the API takes. */
export function encryptItem(vmk: Uint8Array, value: unknown, aad: string) {
  const dek = randomKey();
  try {
    return {
      content_ct: toB64(seal(dek, utf8(JSON.stringify(value)), aad)),
      wrapped_dek: toB64(seal(vmk, dek, `dek|${aad}`)),
    };
  } finally {
    wipe(dek);
  }
}

export function decryptItem<T>(
  vmk: Uint8Array,
  contentCt: string,
  wrappedDek: string,
  aad: string,
): T {
  const dek = open(vmk, fromB64(wrappedDek), `dek|${aad}`);
  try {
    return JSON.parse(sodium.to_string(open(dek, fromB64(contentCt), aad))) as T;
  } finally {
    wipe(dek);
  }
}

/* ─── The condition, for the Transit key ────────────────────────────── */

function pemToDer(pem: string): Uint8Array {
  const body = pem.replace(/-----(BEGIN|END) PUBLIC KEY-----/g, '').replace(/\s+/g, '');
  return fromB64(body);
}

/** RSA-OAEP-SHA256 to the vault's Transit public key (SPKI PEM). */
export async function encryptCondition(publicKeyPem: string, condition: unknown): Promise<string> {
  const key = await crypto.subtle.importKey(
    'spki',
    pemToDer(publicKeyPem) as BufferSource,
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt'],
  );
  const ct = await crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    key,
    utf8(JSON.stringify(condition)) as BufferSource,
  );
  return toB64(new Uint8Array(ct));
}

/** UUIDv7: the device names a thesis before sealing it, because the id is part of the AAD. */
export function uuidv7(now = Date.now()): string {
  const bytes = sodium.randombytes_buf(16);
  const ms = BigInt(now);
  for (let i = 0; i < 6; i++) bytes[i] = Number((ms >> BigInt(8 * (5 - i))) & 0xffn);
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = sodium.to_hex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
