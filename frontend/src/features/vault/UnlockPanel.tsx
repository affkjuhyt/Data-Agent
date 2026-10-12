import { useMemo, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Notice } from '../../design-system/Notice';
import { Textarea } from '../../design-system/Textarea';
import { createVault, type Vault } from '../../lib/v1/exec';
import { isRecoveryCode, newRecoveryCode } from '../../lib/vault/crypto';
import {
  newVaultMaterial,
  unlockWithPasskey,
  unlockWithRecoveryCode,
  type VaultSession,
} from '../../lib/vault/session';

/*
 * Opening the vault on this device (G10.1–G10.2): a passkey when this device holds one of the
 * vault's, otherwise the 24-word paper code. Neither the code nor the key leaves the device; a wrong
 * code fails the AEAD, so the message cannot tell a typo from another vault's code.
 */
export function UnlockPanel({
  vault,
  onUnlocked,
}: {
  vault: Vault;
  onUnlocked: (session: VaultSession) => void;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState<'passkey' | 'code' | null>(null);
  const [error, setError] = useState<string>();
  const passkeys = vault.key_wraps.filter((w) => w.kind !== 'recovery_code' && w.credential_id);

  const run = async (kind: 'passkey' | 'code') => {
    setBusy(kind);
    setError(undefined);
    try {
      onUnlocked(
        kind === 'passkey'
          ? await unlockWithPasskey(vault)
          : await unlockWithRecoveryCode(vault, code),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (vault.status !== 'active')
    return (
      <Notice tone="info">
        Kho đã {vault.status === 'sealed' ? 'niêm phong' : 'huỷ khoá'} theo hợp đồng
        {vault.contract_ref ? ` ${vault.contract_ref}` : ''}; không ghi thêm được.
      </Notice>
    );

  return (
    <section
      aria-label="Mở kho riêng"
      className="flex max-w-[560px] flex-col gap-da-lg rounded-lg border border-border bg-card p-da-xl"
    >
      <div className="flex flex-col gap-da-xs">
        <h2 className="text-foreground da-heading-3">Mở kho trên thiết bị này</h2>
        <p className="text-muted-foreground da-body-small">
          Khoá kho chỉ nằm trên thiết bị của bạn. Máy chủ và quản trị chỉ giữ bản mã, không mở hộ
          được.
        </p>
      </div>
      {passkeys.length > 0 && (
        <Button
          kind="primary"
          icon="key"
          loading={busy === 'passkey'}
          disabled={busy !== null}
          onClick={() => run('passkey')}
          className="self-start"
        >
          Mở bằng passkey
        </Button>
      )}
      <form
        className="flex flex-col gap-da-md"
        onSubmit={(e) => {
          e.preventDefault();
          if (isRecoveryCode(code) && !busy) void run('code');
        }}
      >
        <Field label="Hoặc mã khôi phục giấy (24 từ)" labelTone="muted">
          <Textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            rows={3}
            autoComplete="off"
            spellCheck={false}
            mono
          />
        </Field>
        <Button
          type="submit"
          kind={passkeys.length ? 'secondary' : 'primary'}
          loading={busy === 'code'}
          disabled={!isRecoveryCode(code) || busy !== null}
          className="self-start"
        >
          Mở bằng mã giấy
        </Button>
      </form>
      {error && <Notice tone="error">{error}</Notice>}
    </section>
  );
}

const normalize = (text: string) => text.trim().toLowerCase().split(/\s+/).join(' ');

/*
 * First use (UC-16-01): two paper codes, both offline, so the vault starts with the two wraps BR-189
 * needs. Each code is typed back before the vault is created — a code nobody can read back is a
 * vault nobody can open — and the leader confirms that losing every key loses the content.
 */
export function SetupPanel({ onCreated }: { onCreated: () => void }) {
  const codes = useMemo(() => [newRecoveryCode(), newRecoveryCode()], []);
  const [typed, setTyped] = useState(['', '']);
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const checked = typed.map((t, i) => normalize(t) === codes[i]);
  const month = new Date().toLocaleDateString('vi-VN', { month: '2-digit', year: 'numeric' });

  const create = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const material = await newVaultMaterial(
        codes.map((code, i) => ({
          code,
          label: `Mã khôi phục giấy 24 từ · ${month} · bản ${i + 1}`,
        })),
      );
      await createVault(material);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Tạo kho riêng"
      className="flex max-w-[720px] flex-col gap-da-lg rounded-lg border border-border bg-card p-da-xl"
    >
      <div className="flex flex-col gap-da-xs">
        <h2 className="text-foreground da-heading-3">Tạo kho riêng</h2>
        <p className="text-muted-foreground da-body-small">
          In hoặc chép tay hai mã dưới đây, cất ở hai nơi khác nhau. Mã chỉ hiện một lần; sau khi
          tạo kho bạn thêm passkey ở tab Công bố & khoá.
        </p>
      </div>
      {codes.map((code, i) => (
        <div key={i} className="flex flex-col gap-da-md">
          <p className="text-foreground da-body-strong">Mã khôi phục {i + 1}</p>
          <ol className="grid grid-cols-4 gap-x-da-lg gap-y-da-xs rounded-md bg-subtle p-da-lg da-mono sm:grid-cols-6">
            {code.split(' ').map((word, w) => (
              <li key={w} className="text-foreground">
                <span className="text-muted-foreground">{w + 1}.</span> {word}
              </li>
            ))}
          </ol>
          <Field label={`Gõ lại mã ${i + 1} để thử`} labelTone="muted">
            <Textarea
              value={typed[i]}
              onChange={(e) => setTyped((t) => t.map((v, j) => (j === i ? e.target.value : v)))}
              rows={2}
              autoComplete="off"
              spellCheck={false}
              mono
            />
          </Field>
          {typed[i] && (
            <span
              className={
                checked[i] ? 'text-success da-caption' : 'text-muted-foreground da-caption'
              }
            >
              {checked[i] ? 'Khớp.' : 'Chưa khớp.'}
            </span>
          )}
        </div>
      ))}
      <Checkbox checked={understood} onChange={setUnderstood}>
        Mất mọi khoá thì mất nội dung, quản trị không khôi phục được.
      </Checkbox>
      {error && <Notice tone="error">{error}</Notice>}
      <Button
        kind="primary"
        loading={busy}
        disabled={!checked.every(Boolean) || !understood}
        onClick={create}
        className="self-start"
      >
        Tạo kho
      </Button>
    </section>
  );
}
