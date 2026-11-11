import { useState } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { requestAccess } from '../../lib/v1/adminAccess';
import { V1Error, type ForbiddenAccess } from '../../lib/v1/client';
import { useV1Mutation } from '../../lib/v1/useV1';

/*
 * "v3.1 · Trạng thái · Thiếu quyền" (2076:5267, UC-11-07): what a page shows in place of data the
 * person may not read. It names what was refused and whose it is, never the data; asks for access
 * in place (the level the server offers, a term of at most 90 days — BR-142 — and a reason
 * prefilled from where they came from), and says what the agent can still answer meanwhile.
 */
// At most 90 days (BR-142).
const TERMS = [7, 30, 90].map((d) => ({ value: String(d), label: `${d} ngày` }));

/** A1.2's capabilities in words, for a refusal that names the one missing. */
const CAPABILITY_NAME: Record<string, string> = {
  ask: 'Hỏi đáp',
  view_detail: 'Xem chi tiết',
  upload: 'Tải tệp',
  prepare_data: 'Chuẩn bị dữ liệu',
  admin_data: 'Quản trị dữ liệu',
  steward: 'Phụ trách Data Space',
  approve: 'Duyệt thay đổi',
  create_skill: 'Tạo kỹ năng',
  run_agent: 'Chạy agent',
  create_agent: 'Tạo agent',
  writeback: 'Ghi ngược',
  admin: 'Quản trị',
  compliance: 'Tuân thủ',
  audit_read: 'Đọc nhật ký kiểm toán',
  exec_vault: 'Kho riêng',
};

/*
 * A 403 from a capability check (kernel/auth.py require) carries no access block, only "Missing
 * capability: a or b." — and those roles are org-wide (separation of duties keeps compliance and
 * audit away from admin), granted by an admin rather than asked for in a Data Space. So the
 * refusal names what is missing and offers no request form (no options).
 */
function plainRefusal(error: V1Error, what: string): ForbiddenAccess {
  const missing = /^Missing capability: (.+)\.$/.exec(error.message)?.[1]?.split(' or ') ?? [];
  const needed = missing
    .map((c) => (CAPABILITY_NAME[c] ? `${CAPABILITY_NAME[c]} (${c})` : c))
    .join(' hoặc ');
  return {
    title: `Bạn chưa có quyền xem ${what}`,
    detail: needed
      ? `Cần quyền ${needed}. Quyền này đi theo vai trò trong tổ chức; quản trị viên cấp khi bạn cần.`
      : `Vai trò hiện tại của bạn không được mở phần này. ${error.message}`,
    data_space_id: '',
    owner_name: '',
    options: [],
    reason_hint: '',
    fallback_text: null,
  };
}

/**
 * What a 403 on a read refused: the server's access block when it sent one, otherwise the
 * refusal of a capability check in words. `what` names the refused content, "nhật ký kiểm toán".
 */
export const forbiddenOf = (
  error: Error | undefined,
  what = 'nội dung này',
): ForbiddenAccess | undefined =>
  error instanceof V1Error && error.status === 403
    ? (error.access ?? plainRefusal(error, what))
    : undefined;

const isoDateIn = (days: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

export function AccessDenied({ access }: { access: ForbiddenAccess }) {
  const [capability, setCapability] = useState(access.options[0]?.capability ?? null);
  const [term, setTerm] = useState('30');
  const [reason, setReason] = useState(access.reason_hint);
  // A missing reason is caught here, before the request.
  const [invalid, setInvalid] = useState<string>();
  const request = useV1Mutation((requested: NonNullable<typeof capability>) =>
    requestAccess({
      data_space_id: access.data_space_id,
      capability: requested,
      reason: reason.trim(),
      requested_until: isoDateIn(Number(term)),
    }),
  );
  const busy = request.pending;
  const error = invalid ?? request.error?.message;
  const sent = request.data;

  const send = () => {
    if (!capability) return;
    if (!reason.trim()) {
      setInvalid('Ghi lý do xin quyền.');
      return;
    }
    setInvalid(undefined);
    request.run(capability);
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
      {access.options.length > 0 && (
        <section
          aria-labelledby="access-request-title"
          className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-lg"
        >
          <h2 id="access-request-title" className="text-foreground da-heading-3">
            Yêu cầu quyền
          </h2>
          {sent ? (
            <Notice tone="success">
              Đã gửi yêu cầu {sent.code}. {access.owner_name} nhận trong Cần bạn; bạn được báo khi
              có quyết định.
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
      )}
      {access.fallback_text && (
        <p className="flex items-start gap-da-sm rounded-md border border-border bg-card px-da-md py-da-sm text-foreground da-body-small">
          <Icon name="agent" className="mt-[2px] text-primary" />
          {access.fallback_text}
        </p>
      )}
    </div>
  );
}
