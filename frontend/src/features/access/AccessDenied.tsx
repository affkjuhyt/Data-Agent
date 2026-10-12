import { useState } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { requestAccess, type AccessRequest } from '../../lib/v1/adminAccess';
import { V1Error, type ForbiddenAccess } from '../../lib/v1/client';

/*
 * "v3.1 · Trạng thái · Thiếu quyền" (2076:5267, UC-11-07): what a page shows in place of data the
 * person may not read. It names what was refused and whose it is, never the data; asks for access
 * in place (the level the server offers, a term of at most 90 days — BR-142 — and a reason
 * prefilled from where they came from), and says what the agent can still answer meanwhile.
 */
// At most 90 days (BR-142).
const TERMS = [7, 30, 90].map((d) => ({ value: String(d), label: `${d} ngày` }));

/** The access block of a 403 on a read, when the server sent one. */
export const forbiddenOf = (error: Error | undefined): ForbiddenAccess | undefined =>
  error instanceof V1Error && error.status === 403 ? error.access : undefined;

const isoDateIn = (days: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

export function AccessDenied({ access }: { access: ForbiddenAccess }) {
  const [capability, setCapability] = useState(access.options[0]?.capability ?? null);
  const [term, setTerm] = useState('30');
  const [reason, setReason] = useState(access.reason_hint);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [sent, setSent] = useState<AccessRequest>();

  const send = async () => {
    if (!capability) return;
    if (!reason.trim()) {
      setError('Ghi lý do xin quyền.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      setSent(
        await requestAccess({
          data_space_id: access.data_space_id,
          capability,
          reason: reason.trim(),
          requested_until: isoDateIn(Number(term)),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex w-[672px] max-w-full flex-col gap-da-md px-da-lg py-da-3xl">
      <section className="flex flex-col items-center gap-da-md rounded-md border border-border bg-card px-da-xl py-da-xl text-center">
        <span className="inline-flex size-12 items-center justify-center rounded-full bg-subtle text-foreground">
          <Icon name="lock" />
        </span>
        <h1 className="text-foreground da-heading-2">{access.title}</h1>
        <p className="text-muted-foreground da-body">{access.detail}</p>
      </section>
      <section
        aria-labelledby="access-request-title"
        className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-lg"
      >
        <h2 id="access-request-title" className="text-foreground da-heading-3">
          Yêu cầu quyền
        </h2>
        {sent ? (
          <Notice tone="success">
            Đã gửi yêu cầu {sent.code}. {access.owner_name} nhận trong Cần bạn; bạn được báo khi có
            quyết định.
          </Notice>
        ) : (
          <>
            {error && <Notice tone="error">Không gửi được: {error}</Notice>}
            <Field label="Mức quyền">
              <Select<ForbiddenAccess['options'][number]['capability']>
                label="Mức quyền"
                value={capability}
                onChange={setCapability}
                options={access.options.map((o) => ({ value: o.capability, label: o.label }))}
              />
            </Field>
            <Field label="Thời hạn">
              <Select label="Thời hạn" value={term} onChange={setTerm} options={TERMS} />
            </Field>
            <Field label="Lý do">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
            <div>
              <Button kind="primary" loading={busy} disabled={busy || !capability} onClick={send}>
                Gửi yêu cầu
              </Button>
            </div>
          </>
        )}
      </section>
      {access.fallback_text && (
        <p className="flex items-start gap-da-sm rounded-md border border-border bg-card px-da-md py-da-sm text-foreground da-body-small">
          <Icon name="agent" className="mt-[2px] text-primary" />
          {access.fallback_text}
        </p>
      )}
    </div>
  );
}
