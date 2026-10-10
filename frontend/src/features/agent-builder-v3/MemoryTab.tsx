import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  addMemory,
  forgetMemory,
  listMemories,
  type AgentMemory,
  type MemoryScope,
} from '../../lib/v1/agentBuilder';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P13 · Agent · Bộ nhớ" (2310:20345, UC-13-07): what the agent learnt from people, with where it
 * came from, how far it applies and when it was last used; "Quên" deletes one. A self-learnt or
 * Data Space-wide memory only applies once the Data Space owner approves it, so it shows
 * "chờ duyệt" in warning.
 */
const SCOPES: { value: MemoryScope; label: string }[] = [
  { value: 'agent', label: 'Agent này' },
  { value: 'user', label: 'Riêng tôi' },
  { value: 'data_space', label: 'Data Space (cần chủ sở hữu duyệt)' },
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
      {adding && (
        <NewMemoryForm
          agentId={agentId}
          onDone={(saved) => {
            onAddingDone();
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
                <th className="w-[60px] px-da-lg py-da-md">
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
                      <button
                        type="button"
                        onClick={() => setForgetting(memory)}
                        aria-label={`Quên: ${memory.content}`}
                        className="rounded-sm text-primary da-body-strong hover:underline"
                      >
                        Quên
                      </button>
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

function NewMemoryForm({ agentId, onDone }: { agentId: string; onDone: (saved: boolean) => void }) {
  const toast = useToast();
  const [content, setContent] = useState('');
  const [scope, setScope] = useState<MemoryScope>('agent');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const save = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const memory = await addMemory(agentId, { scope, kind: 'domain_context', content: content.trim() });
      toast({
        tone: 'success',
        message: memory.status === 'pending_review' ? 'Đã gửi ghi nhớ cho chủ sở hữu Data Space duyệt' : 'Đã thêm ghi nhớ',
      });
      onDone(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="flex flex-col gap-da-md rounded-lg border border-border p-da-lg"
      onSubmit={(event) => {
        event.preventDefault();
        if (content.trim()) void save();
      }}
    >
      <div className="flex items-end gap-da-md max-[1023px]:flex-col max-[1023px]:items-stretch">
        <div className="min-w-0 flex-1">
          <Field label="Ghi nhớ">
            <Input
              autoFocus
              className="w-full"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="Ví dụ: “Miền Nam” gồm Đông Nam Bộ và ĐB sông Cửu Long"
            />
          </Field>
        </div>
        <div className="w-[260px] max-[1023px]:w-full">
          <Field label="Phạm vi">
            <Select<MemoryScope> label="Phạm vi" options={SCOPES} value={scope} onChange={setScope} />
          </Field>
        </div>
        <Button kind="ghost" onClick={() => onDone(false)}>
          Huỷ
        </Button>
        <Button kind="primary" type="submit" disabled={!content.trim()} loading={saving}>
          Lưu ghi nhớ
        </Button>
      </div>
      {error && <Notice tone="error">Không lưu được: {error}</Notice>}
    </form>
  );
}
