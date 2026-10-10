import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { listAgents } from '../../lib/v1/api';
import { registerTool, type McpServer, type Tool, type ToolLevel } from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../sessions/FormDialog';
import { TOOL_LEVELS } from './labels';

/*
 * "Hộp thoại · registerTool" (2095:8479, UC-13-02): name, where it comes from (built in or an
 * MCP server), permission level, the description the agent reads, and the agent allowed to
 * call it. A system-level tool is never given to an agent, so the agent field goes away.
 */
const LEVEL_OPTIONS = TOOL_LEVELS.map((l) => ({
  value: l.level,
  label:
    l.level === 'read'
      ? l.label
      : l.level === 'system'
        ? `${l.label} · chỉ quản trị viên`
        : `${l.label} · cần duyệt`,
}));

const NO_AGENT = 'none';

export function RegisterToolDialog({
  open,
  servers,
  onClose,
  onRegistered,
}: {
  open: boolean;
  servers: McpServer[];
  onClose: () => void;
  onRegistered: (tool: Tool) => void;
}) {
  const agents = useV1(open ? listAgents : null, [open]);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState<string | null>(null);
  const [level, setLevel] = useState<ToolLevel | null>(null);
  const [description, setDescription] = useState('');
  const [agent, setAgent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setName('');
    setProvider(null);
    setLevel(null);
    setDescription('');
    setAgent(null);
    setError(undefined);
  }, [open]);

  const providers = [
    ...servers
      .filter((s) => s.status === 'active')
      .map((s) => ({ value: s.id, label: `MCP · máy chủ ${s.name}` })),
    { value: 'builtin', label: 'Có sẵn trong nền tảng' },
  ];
  const agentOptions = [
    ...(agents.data?.items ?? []).map((a) => ({ value: a.id, label: a.name })),
    { value: NO_AGENT, label: 'Chưa gán agent' },
  ];
  const system = level === 'system';

  const submit = () => {
    if (!name.trim() || !provider || !level || !description.trim()) {
      setError('Điền tên, loại, mức quyền và mô tả cho agent.');
      return;
    }
    setBusy(true);
    setError(undefined);
    registerTool({
      name: name.trim(),
      provider,
      level,
      description: description.trim(),
      agent_ids: system || !agent || agent === NO_AGENT ? [] : [agent],
    })
      .then(onRegistered)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <FormDialog
      open={open}
      title="Đăng ký công cụ"
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} onClick={submit}>
            Đăng ký
          </Button>
        </>
      }
    >
      <p className="-mt-da-sm text-muted-foreground da-body-small">
        Công cụ là hành động agent được phép gọi; mức quyền quyết định chính sách duyệt.
      </p>
      <Field label="Tên">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="ticket.create" />
      </Field>
      <Field label="Loại">
        <Select label="Loại" options={providers} value={provider} onChange={setProvider} />
      </Field>
      <Field label="Mức quyền">
        <Select<ToolLevel>
          label="Mức quyền"
          options={LEVEL_OPTIONS}
          value={level}
          onChange={setLevel}
        />
      </Field>
      <Field label="Mô tả cho agent">
        <Input value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {system ? (
        <p className="text-muted-foreground da-caption">
          Công cụ hệ thống chỉ quản trị viên dùng; agent không được gán.
        </p>
      ) : (
        <Field label="Agent được dùng">
          <Select
            label="Agent được dùng"
            options={agentOptions}
            value={agent}
            onChange={setAgent}
          />
        </Field>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </FormDialog>
  );
}
