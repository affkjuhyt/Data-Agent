import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  getSharing,
  previewSharing,
  saveSharing,
  searchPrincipals,
  type MemberRole,
  type Principal,
  type WorkspaceDetail,
  type WorkspaceMember,
} from '../../lib/v1/sessions';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from './FormDialog';

/*
 * "P02 · Phiên · Chia sẻ" (2297:18988, UC-02-03): add people or groups, give each a role, and
 * read what each recipient will see before sharing — a recipient with a narrower data scope sees
 * only their part, and the dialog says so (the server computes it from their scope).
 */
const ROLES: { value: MemberRole; label: string }[] = [
  { value: 'owner', label: 'Sở hữu' },
  { value: 'editor', label: 'Sửa' },
  { value: 'viewer', label: 'Xem' },
];

const toInput = (members: WorkspaceMember[]) =>
  members.map((m) => ({ principal_id: m.principal_id, kind: m.kind, role: m.role }));

export function ShareDialog({
  open,
  workspace,
  onClose,
  onShared,
}: {
  open: boolean;
  workspace: WorkspaceDetail;
  onClose: () => void;
  onShared: () => void;
}) {
  const sharing = useV1(open ? () => getSharing(workspace.id) : null, [open, workspace.id]);
  const toast = useToast();
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [preview, setPreview] = useState('');
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<Principal[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!sharing.data) return;
    setMembers(sharing.data.members);
    setPreview(sharing.data.preview);
  }, [sharing.data]);

  useEffect(() => {
    if (!open) {
      setMembers(null);
      setQuery('');
      setError(undefined);
    }
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setMatches([]);
      return;
    }
    let alive = true;
    searchPrincipals(q)
      .then((page) => alive && setMatches(page.items))
      .catch(() => alive && setMatches([]));
    return () => {
      alive = false;
    };
  }, [query]);

  const changeMembers = (next: WorkspaceMember[]) => {
    setMembers(next);
    previewSharing(workspace.id, toInput(next))
      .then((r) => setPreview(r.preview))
      .catch(() => setPreview(''));
  };

  const add = (p: Principal) => {
    if (!members) return;
    changeMembers([
      ...members,
      {
        principal_id: p.id,
        kind: p.kind,
        name: p.name,
        detail: p.detail,
        scope_limited: p.scope_limited,
        role: 'viewer',
      },
    ]);
    setQuery('');
  };

  const share = async () => {
    if (!members) return;
    setSaving(true);
    setError(undefined);
    try {
      await saveSharing(workspace.id, toInput(members));
      toast({ tone: 'success', message: `Đã chia sẻ “${workspace.title}”` });
      onShared();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const copyLink = async () => {
    const link = `${window.location.origin}${sharing.data?.link ?? window.location.pathname}`;
    try {
      await navigator.clipboard.writeText(link);
      toast({ tone: 'success', message: 'Đã sao chép liên kết' });
    } catch {
      toast({ tone: 'error', message: 'Không sao chép được liên kết' });
    }
  };

  const available = matches.filter((p) => !members?.some((m) => m.principal_id === p.id));

  return (
    <FormDialog
      open={open}
      title={`Chia sẻ “${workspace.title}”`}
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            onClick={copyLink}
            className="text-primary da-body-strong hover:underline"
          >
            Sao chép liên kết
          </button>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={saving} disabled={!members} onClick={share}>
            Chia sẻ
          </Button>
        </>
      }
    >
      <div className="relative">
        <Input
          aria-label="Thêm người, nhóm"
          placeholder="Thêm người, nhóm…"
          className="w-full"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {available.length > 0 && (
          <ul
            role="listbox"
            aria-label="Người và nhóm"
            className="absolute left-0 right-0 top-full z-[var(--z-popover)] mt-da-xs flex flex-col rounded-md border border-border bg-card p-da-xs shadow-md"
          >
            {available.map((p) => (
              <li key={p.id} role="option" aria-selected={false}>
                <button
                  type="button"
                  onClick={() => add(p)}
                  className="flex w-full flex-col rounded-sm px-da-md py-da-sm text-left hover:bg-subtle"
                >
                  <span className="text-foreground da-body-strong">{p.name}</span>
                  <span className="text-muted-foreground da-caption">{p.detail}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {sharing.error && (
        <Notice tone="error">Không tải được danh sách chia sẻ. {sharing.error.message}</Notice>
      )}

      {members && (
        <ul className="flex flex-col">
          {members.map((m) => (
            <li
              key={m.principal_id}
              className="flex items-center gap-[10px] border-b border-border py-da-md"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-[2px]">
                <span className="text-foreground da-body-strong">{m.name}</span>
                <span
                  className={cn(
                    'da-caption',
                    m.scope_limited ? 'text-warning' : 'text-muted-foreground',
                  )}
                >
                  {m.detail}
                </span>
              </span>
              <Select<MemberRole>
                label={`Vai trò của ${m.name}`}
                options={ROLES}
                value={m.role}
                onChange={(role) =>
                  changeMembers(
                    members.map((x) => (x.principal_id === m.principal_id ? { ...x, role } : x)),
                  )
                }
                className="w-[112px]"
              />
            </li>
          ))}
        </ul>
      )}

      {preview && (
        <div className="flex flex-col gap-da-sm rounded-md bg-background px-[14px] py-[10px]">
          <span className="text-foreground da-body-strong">Người nhận thấy gì</span>
          <p className="text-foreground da-body-small">{preview}</p>
        </div>
      )}

      {error && <p className="text-error da-caption">{error}</p>}
    </FormDialog>
  );
}
