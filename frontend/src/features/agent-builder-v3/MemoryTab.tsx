import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Dialog } from '../../design-system/Dialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  addMemory,
  forgetMemory,
  listMemories,
  updateMemory,
  type AgentMemory,
  type MemoryExpiry,
  type MemoryScope,
} from '../../lib/v1/agentBuilder';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P13 · Agent · Bộ nhớ" (2310:20345, UC-13-07): what the agent learnt from people, with where it
 * came from, how far it applies and when it was last used; "Sửa" opens the edit dialog, "Quên" deletes one. A self-learnt or
 * Data Space-wide memory only applies once the Data Space owner approves it, so it shows
 * "chờ duyệt" in warning.
 */
const SCOPES: { value: MemoryScope; label: string }[] = [
  { value: 'agent', label: 'Riêng agent này' },
  { value: 'user', label: 'Riêng với tôi' },
  { value: 'data_space', label: 'Data Space (cần chủ sở hữu duyệt)' },
];

// copy: only "Sở thích người dùng" is drawn (2095:8909); the other three labels are provisional.
const KINDS: { value: AgentMemory['kind']; label: string }[] = [
  { value: 'preference', label: 'Sở thích người dùng' },
  { value: 'domain_context', label: 'Kiến thức nghiệp vụ' },
  { value: 'data_insight', label: 'Hiểu biết về dữ liệu' },
  { value: 'decision', label: 'Quyết định' },
];

// copy: "90 ngày" and "Tới khi sai 2 lần" are drawn; "30 ngày" and "Không hết hạn" are provisional.
const EXPIRIES: { value: MemoryExpiry; label: string }[] = [
  { value: 'days_30', label: '30 ngày' },
  { value: 'days_90', label: '90 ngày' },
  { value: 'until_wrong_2', label: 'Tới khi sai 2 lần' },
  { value: 'never', label: 'Không hết hạn' },
];

const pad = (n: number) => String(n).padStart(2, '0');

/** "hôm nay 10:20", "hôm qua", "06/10", "—" as in the design. */
export function lastUsedText(iso: string | null, now = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(now) - day(d)) / 86_400_000);
  if (days === 0) return `hôm nay ${formatTime(d)}`;
  if (days === 1) return 'hôm qua';
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
}

export function MemoryTab({ agentId, adding, onAddingDone }: { agentId: string; adding: boolean; onAddingDone: () => void }) {
  const toast = useToast();
  const memories = useV1(() => listMemories(agentId), [agentId]);
  const [forgetting, setForgetting] = useState<AgentMemory | null>(null);
  const [editing, setEditing] = useState<AgentMemory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const forget = async () => {
    if (!forgetting) return;
    setBusy(true);
    setError(undefined);
    try {
      await forgetMemory(forgetting.id);
      toast({ tone: 'success', message: 'Agent đã quên ghi nhớ này' });
      setForgetting(null);
      memories.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-lg px-da-3xl pb-da-2xl pt-da-xl">
      <p className="text-muted-foreground da-body-small">
        Agent ghi nhớ những điều người dùng dạy nó để lần sau làm đúng hơn. Mỗi ghi nhớ có nguồn gốc,
        phạm vi và có thể xoá.
      </p>
      {(adding || editing) && (
        <MemoryDialog
          agentId={agentId}
          memory={editing}
          onDone={(saved) => {
            if (editing) setEditing(null);
            else onAddingDone();
            if (saved) memories.reload();
          }}
        />
      )}
      <AsyncRegion
        state={memories}
        what="bộ nhớ của agent"
        skeletonRows={4}
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState
            icon="bookmark"
            title="Agent chưa ghi nhớ gì"
            description="Khi bạn sửa cách agent hiểu hoặc đóng một cảnh báo là báo nhầm, agent ghi nhớ để lần sau làm đúng hơn."
          />
        }
      >
        {(page) => (
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Ghi nhớ</th>
                <th className="w-[230px] px-da-lg py-da-md font-normal">Từ đâu</th>
                <th className="w-[150px] px-da-lg py-da-md font-normal">Phạm vi</th>
                <th className="w-[120px] px-da-lg py-da-md font-normal">Dùng gần nhất</th>
                <th className="w-[110px] px-da-lg py-da-md">
                  <span className="sr-only">Thao tác</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((memory) => {
                const pending = memory.status === 'pending_review';
                return (
                  <tr key={memory.id} className="border-b border-border align-middle da-body-small">
                    <td className="px-da-lg py-[9px] text-foreground da-body-strong">{memory.content}</td>
                    <td className="px-da-lg py-[9px] text-muted-foreground">{memory.source_text}</td>
                    <td className={cn('px-da-lg py-[9px]', pending ? 'text-warning' : 'text-foreground')}>
                      {pending ? `${memory.scope_text} · chờ duyệt` : memory.scope_text}
                    </td>
                    <td className="px-da-lg py-[9px] text-muted-foreground">{lastUsedText(memory.last_used_at)}</td>
                    <td className="px-da-lg py-[9px]">
                      <span className="flex gap-da-md">
                      <button
                        type="button"
                        onClick={() => setEditing(memory)}
                        aria-label={`Sửa: ${memory.content}`}
                        className="rounded-sm text-primary da-body-strong hover:underline"
                      >
                        Sửa
                      </button>
                      <button
                        type="button"
                        onClick={() => setForgetting(memory)}
                        aria-label={`Quên: ${memory.content}`}
                        className="rounded-sm text-primary da-body-strong hover:underline"
                      >
                        Quên
                      </button>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </AsyncRegion>
      <p className="text-muted-foreground da-caption">
        Ghi nhớ tự học chỉ có hiệu lực sau khi chủ sở hữu Data Space duyệt; ghi nhớ toàn tổ chức cần
        thêm quản trị viên.
      </p>
      <ConfirmDialog
        open={forgetting !== null}
        title="Quên ghi nhớ này?"
        confirmLabel="Quên"
        loading={busy}
        error={error}
        onConfirm={forget}
        onCancel={() => {
          setForgetting(null);
          setError(undefined);
        }}
      >
        Từ lượt chạy sau, agent không dùng “{forgetting?.content}” nữa. Không hoàn tác được; muốn dùng
        lại thì phải thêm lại.
      </ConfirmDialog>
    </div>
  );
}

/*
 * "Hộp thoại · addMemory" (2095:8909) and "Hộp thoại · editMemory" (2095:8961): the same fields,
 * except that the kind of a memory is fixed once it exists.
 */
function MemoryDialog({
  agentId,
  memory,
  onDone,
}: {
  agentId: string;
  /** null = a new memory. */
  memory: AgentMemory | null;
  onDone: (saved: boolean) => void;
}) {
  const toast = useToast();
  const editing = memory !== null;
  const [content, setContent] = useState(memory?.content ?? '');
  const [kind, setKind] = useState<AgentMemory['kind']>(memory?.kind ?? 'preference');
  const [scope, setScope] = useState<MemoryScope>(memory?.scope ?? 'agent');
  const [expiry, setExpiry] = useState<MemoryExpiry>(memory?.expiry ?? 'days_90');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const save = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const saved = editing
        ? await updateMemory(memory.id, { content: content.trim(), expiry })
        : await addMemory(agentId, { scope, kind, content: content.trim(), expiry });
      toast({
        tone: 'success',
        message:
          saved.status === 'pending_review' && memory?.status !== 'pending_review'
            ? 'Đã gửi ghi nhớ cho chủ sở hữu Data Space duyệt'
            : editing
              ? 'Đã lưu ghi nhớ'
              : 'Đã thêm ghi nhớ',
      });
      onDone(true);
    } catch (e) {
      setError(`Không lưu được: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      title={editing ? 'Sửa ghi nhớ' : 'Thêm ghi nhớ'}
      description={
        editing
          ? 'Thay đổi áp dụng cho lượt chạy tiếp theo.'
          : 'Ghi nhớ giúp agent làm đúng ý bạn ở lượt sau.'
      }
      submitLabel={editing ? 'Lưu' : 'Lưu ghi nhớ'}
      submitting={saving}
      canSubmit={content.trim() !== ''}
      error={error}
      onSubmit={save}
      onClose={() => onDone(false)}
    >
      <Field label="Nội dung" labelTone="muted">
        {editing ? (
          <Textarea autoFocus rows={3} value={content} onChange={(e) => setContent(e.target.value)} />
        ) : (
          <Input
            autoFocus
            className="w-full"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="Ví dụ: “Miền Nam” gồm Đông Nam Bộ và ĐB sông Cửu Long"
          />
        )}
      </Field>
      {!editing && (
        <Field label="Loại" labelTone="muted">
          <Select<AgentMemory['kind']> label="Loại" options={KINDS} value={kind} onChange={setKind} />
        </Field>
      )}
      {/* The scope is fixed once the memory exists (MemoryPatch has no scope). */}
      {editing ? (
        <Field label="Phạm vi" labelTone="muted">
          <span className="text-foreground da-body-small">{memory?.scope_text}</span>
        </Field>
      ) : (
        <Field label="Phạm vi" labelTone="muted">
          <Select<MemoryScope> label="Phạm vi" options={SCOPES} value={scope} onChange={setScope} />
        </Field>
      )}
      <Field label="Hạn" labelTone="muted">
        <Select<MemoryExpiry> label="Hạn" options={EXPIRIES} value={expiry} onChange={setExpiry} />
      </Field>
    </Dialog>
  );
}
