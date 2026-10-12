import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "Composer" (2018:157) and its spec: radius-2xl card with a strong border, an optional
 * context chip on accent, a DA/Body field growing from 48 to 200px, and a 32px primary send button.
 * Enter sends, Shift+Enter breaks the line, ⌘/Ctrl+Enter runs in the background.
 * Typing stays enabled while an agent runs, to steer it.
 */
const MAX_HEIGHT = 200;

export function Composer({
  placeholder,
  context,
  sending = false,
  onSubmit,
  className,
}: {
  placeholder: string;
  /** "Đang chọn: Điện lạnh · cột Δ biên LN" */
  context?: string;
  sending?: boolean;
  onSubmit: (text: string, options: { background: boolean }) => void;
  className?: string;
}) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);

  const submit = (background: boolean) => {
    const value = text.trim();
    if (!value || sending) return;
    onSubmit(value, { background });
    setText('');
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit(event.metaKey || event.ctrlKey);
  };

  return (
    <div
      className={cn(
        'flex flex-col gap-da-md rounded-2xl border border-border-strong bg-card px-da-lg py-da-md focus-within:border-ring',
        className,
      )}
    >
      {context && (
        <span className="inline-flex w-fit items-center gap-da-xs rounded-full bg-accent px-da-md py-da-xs text-primary da-label">
          <Icon name="eye" />
          {context}
        </span>
      )}
      <div className="flex items-end gap-da-md">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
          aria-label={placeholder}
          className="min-h-8 flex-1 resize-none bg-transparent py-1.5 text-foreground outline-none da-body placeholder:text-muted-foreground focus-visible:shadow-none"
        />
        <button
          type="button"
          aria-label="Gửi"
          aria-busy={sending || undefined}
          onClick={() => submit(false)}
          className={cn(
            'inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary-hover',
            (sending || !text.trim()) && 'opacity-85',
          )}
        >
          <Icon
            name={sending ? 'refresh' : 'arrow-up'}
            className={sending ? 'animate-spin' : undefined}
          />
        </button>
      </div>
    </div>
  );
}
