import { beforeAll, describe, expect, it } from 'vitest';
import {
  decryptItem,
  encryptCondition,
  encryptItem,
  isRecoveryCode,
  itemAad,
  newBoxPair,
  newRecoveryCode,
  newSalt,
  openBoxPrivate,
  openSealed,
  parseKdf,
  randomKey,
  ready,
  RECOVERY_KDF,
  sealForBox,
  unwrapVmk,
  uuidv7,
  wrapKeyFromPrf,
  wrapKeyFromRecovery,
  wrapVmk,
} from './crypto';

beforeAll(async () => {
  await ready;
});

describe('vault crypto', () => {
  it('opens the VMK with the paper code at the spec KDF, and not with another code', () => {
    const vmk = randomKey();
    const code = newRecoveryCode();
    expect(code.split(' ')).toHaveLength(24);
    expect(isRecoveryCode(code)).toBe(true);
    const salt = newSalt();
    const started = Date.now();
    const key = wrapKeyFromRecovery(code, salt, { kind: 'argon2id', ...RECOVERY_KDF });
    console.info(`argon2id m=256MB t=3: ${Date.now() - started} ms`);
    const wrapped = wrapVmk(key, vmk);
    // Case and spacing do not matter: the key comes from the code's entropy.
    const again = wrapKeyFromRecovery(`  ${code.toUpperCase()} `, salt, {
      kind: 'argon2id',
      ...RECOVERY_KDF,
    });
    expect(unwrapVmk(again, wrapped)).toEqual(vmk);
    const other = wrapKeyFromRecovery(newRecoveryCode(), salt, {
      kind: 'argon2id',
      opsLimit: 1,
      memLimit: 8 * 1024 * 1024,
    });
    expect(() => unwrapVmk(other, wrapped)).toThrow();
  }, 30_000);

  it('derives the same wrap key from the same PRF output and salt', async () => {
    const prf = randomKey();
    const salt = newSalt();
    expect(await wrapKeyFromPrf(prf, salt)).toEqual(await wrapKeyFromPrf(prf, salt));
    expect(await wrapKeyFromPrf(prf, newSalt())).not.toEqual(await wrapKeyFromPrf(prf, salt));
  });

  it('binds an item to its id, owner and version', () => {
    const vmk = randomKey();
    const aad = itemAad('t-1', 'u-1', 1);
    const { content_ct, wrapped_dek } = encryptItem(
      vmk,
      { statement: 'Giá xăng E5 kỳ 22/10 ≥ 20.000', confidence: 0.7 },
      aad,
    );
    expect(decryptItem(vmk, content_ct, wrapped_dek, aad)).toEqual({
      statement: 'Giá xăng E5 kỳ 22/10 ≥ 20.000',
      confidence: 0.7,
    });
    expect(() => decryptItem(vmk, content_ct, wrapped_dek, itemAad('t-1', 'u-2', 1))).toThrow();
    expect(() => decryptItem(randomKey(), content_ct, wrapped_dek, aad)).toThrow();
  });

  it('opens what a workflow sealed to the vault, with the private key the VMK unwraps', () => {
    const vmk = randomKey();
    const pair = newBoxPair(vmk);
    const sealed = sealForBox(pair.publicKey, new TextEncoder().encode('{"side":"oppose"}'));
    const privateKey = openBoxPrivate(vmk, pair.privateCt);
    expect(new TextDecoder().decode(openSealed(pair.publicKey, privateKey, sealed))).toBe(
      '{"side":"oppose"}',
    );
  });

  it('encrypts the condition to an RSA-OAEP-SHA256 public key the holder can decrypt', async () => {
    const pair = await crypto.subtle.generateKey(
      {
        name: 'RSA-OAEP',
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: 'SHA-256',
      },
      true,
      ['encrypt', 'decrypt'],
    );
    const spki = new Uint8Array(await crypto.subtle.exportKey('spki', pair.publicKey));
    const pem = `-----BEGIN PUBLIC KEY-----\n${btoa(String.fromCharCode(...spki))}\n-----END PUBLIC KEY-----`;
    const ct = await encryptCondition(pem, { comparator: 'gte', threshold: 20000 });
    const plain = await crypto.subtle.decrypt(
      { name: 'RSA-OAEP' },
      pair.privateKey,
      Uint8Array.from(atob(ct), (c) => c.charCodeAt(0)),
    );
    expect(JSON.parse(new TextDecoder().decode(plain))).toEqual({
      comparator: 'gte',
      threshold: 20000,
    });
  });

  it('writes UUIDv7 and reads the KDF a wrap recorded', () => {
    expect(uuidv7(Date.UTC(2026, 9, 11))).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(parseKdf('argon2id')).toEqual({
      kind: 'argon2id',
      opsLimit: 3,
      memLimit: 268435456,
    });
    expect(parseKdf('hkdf_sha256')).toEqual({ kind: 'prf-hkdf' });
    expect(() => parseKdf('argon2id;t=3;m=268435456')).toThrow();
  });
});
