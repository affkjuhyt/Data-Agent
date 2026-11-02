import { useRef, useState } from 'react';
import { Icon } from '../../../design-system/Icon';
import type { ExploreModel } from '../../../lib/v1/explore';
import { cn } from '../../../lib/utils';
import { CHAT_COMMANDS } from '../chatCommands';

/*
 * The question box (design C3): "@" pins a metric or dimension of the Data Space as a chip above
 * the field, "/" lists the chat commands. On send the chips' names go in front of the text, so
 * the reader of the question sees them as written words — no new request field. Enter sends,
 * Shift+Enter breaks the line; "Dừng" stops the answer coming in.
 */

interface Pin {
  kind: 'metric' | 'dimension';
  label: string;
}

const MAX_HEIGHT = 200;

export function ChatComposer({
  sending,
  onSend,
  onStop,
  model,
  placeholder = 'Hỏi về dữ liệu, gõ @ để thêm ngữ cảnh và / để dùng lệnh',
  autoFocus,
}: {
  sending: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  model: ExploreModel | null | undefined;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState('');
  const [pins, setPins] = useState<Pin[]>([]);
  const ref = useRef<HTMLTextAreaElement>(null);

  const trimmed = text.trimStart();
  const mention = /(?:^|\s)@([^\s@]*)$/.exec(text);
  const mentionQuery = mention ? mention[1].toLowerCase() : null;
  const candidates =
    mentionQuery === null
      ? []
      : [
          ...(model?.metrics ?? []).map((m) => ({ kind: 'metric' as const, label: m.label })),
          ...(model?.dimensions ?? []).map((d) => ({ kind: 'dimension' as const, label: d.label })),
        ]
          .filter((c) => c.label.toLowerCase().includes(mentionQuery) && !pins.some((p) => p.label === c.label))
          .slice(0, 8);
  const commands =
    trimmed.startsWith('/') && !trimmed.includes(' ')
      ? CHAT_COMMANDS.filter((c) => c.label.startsWith(trimmed.toLowerCase()))
      : [];

  const resize = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  };

  const pin = (p: Pin) => {
    setPins((prev) => [...prev, p]);
    setText((prev) => prev.replace(/@[^\s@]*$/, '').trimEnd());
    ref.current?.focus();
  };

  const submit = () => {
    const body = text.trim();
    if (!body && pins.length === 0) return;
    // A command keeps its leading "/": the chips go after it, before the rest.
    const command = /^\/\S+/.exec(body)?.[0];
    const rest = command ? body.slice(command.length).trim() : body;
    const words = [...pins.map((p) => p.label), rest].filter(Boolean).join(' ');
    onSend(command ? `${command} ${words}`.trim() : words);
    setText('');
    setPins([]);
    if (ref.current) ref.current.style.height = 'auto';
  };

  return (
    <div className="relative flex flex-col gap-da-xs rounded-xl border border-border-strong bg-card px-da-md py-da-sm shadow-sm focus-within:border-ring">
      {(candidates.length > 0 || commands.length > 0) && (
        <div
          role="listbox"
          aria-label={commands.length > 0 ? 'Lệnh chat' : 'Ngữ cảnh'}
          className="absolute bottom-full left-0 z-[var(--z-popover)] mb-da-xs w-full max-w-[480px] rounded-md border border-border bg-card p-da-xs shadow-md"
        >
          {commands.map((c) => (
            <button
              key={c.name}
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => {
                setText(`${c.label} `);
                ref.current?.focus();
              }}
              className="flex w-full items-center gap-da-md rounded-sm px-da-md py-da-sm text-left hover:bg-subtle"
            >
              <span className="text-foreground da-body-strong">{c.label}</span>
              <span className="text-muted-foreground da-caption">{c.description}</span>
            </button>
          ))}
          {candidates.map((c) => (
            <button
              key={`${c.kind}-${c.label}`}
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => pin(c)}
              className="flex w-full items-center gap-da-sm rounded-sm px-da-md py-da-sm text-left hover:bg-subtle"
            >
              <Icon name={c.kind === 'metric' ? 'chart' : 'layers'} size={14} className="text-muted-foreground" />
              <span className="text-foreground da-body-small">{c.label}</span>
              <span className="text-muted-foreground da-caption">{c.kind === 'metric' ? 'chỉ số' : 'chiều'}</span>
            </button>
          ))}
        </div>
      )}
      {pins.length > 0 && (
        <div className="flex flex-wrap gap-da-xs">
          {pins.map((p) => (
            <span key={p.label} className="inline-flex items-center gap-[4px] rounded-sm bg-accent px-[6px] py-[2px] text-primary da-caption">
              <Icon name={p.kind === 'metric' ? 'chart' : 'layers'} size={12} />
              {p.label}
              <button
                type="button"
                aria-label={`Bỏ ${p.label}`}
                onClick={() => setPins((prev) => prev.filter((x) => x.label !== p.label))}
                className="inline-flex"
              >
                <Icon name="x" size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex items-end gap-da-sm">
        <textarea
          ref={ref}
          value={text}
          rows={1}
          autoFocus={autoFocus}
          aria-label="Câu hỏi"
          placeholder={sending ? 'Đang trả lời… gõ tiếp để xếp câu hỏi' : placeholder}
          onChange={(e) => {
            setText(e.target.value);
            resize();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          className="max-h-[200px] min-h-8 flex-1 resize-none bg-transparent py-1.5 text-foreground outline-none da-body placeholder:text-muted-foreground"
        />
        {sending ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Dừng"
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-subtle text-foreground hover:bg-border"
          >
            <Icon name="x" />
          </button>
        ) : null}
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim() && pins.length === 0}
          aria-label="Gửi câu hỏi"
          className={cn(
            'inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary-hover',
            'disabled:bg-subtle disabled:text-muted-foreground',
          )}
        >
          <Icon name="arrow-up" />
        </button>
      </div>
    </div>
  );
}
