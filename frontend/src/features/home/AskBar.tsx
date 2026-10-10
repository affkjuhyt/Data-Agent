import { useState } from 'react';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';

/*
 * The question bar of "v3.1 · Trang chủ" (2132:15338) — "Spec · v3.1 · Thanh hỏi":
 * a card with a strong border and radius 10, padding 12×16; a search mark, the field in
 * DA/Reading, the Primary "Hỏi"; below it, suggestion chips (border, radius 10, 4×8, 13/18).
 * Enter asks. Suggestions are the viewer's own recent questions; none, no chips.
 */
export function AskBar({
  suggestions,
  onAsk,
}: {
  suggestions: string[];
  onAsk: (question: string) => void;
}) {
  const [text, setText] = useState('');
  const submit = () => {
    const q = text.trim();
    if (q) onAsk(q);
  };
  return (
    <section
      aria-label="Hỏi"
      className="flex w-full flex-col gap-da-lg rounded-lg border border-border-strong bg-card px-da-xl py-da-lg"
    >
      <form
        className="flex items-center gap-da-lg"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Icon name="search" className="text-muted-foreground" />
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Hỏi về dữ liệu, hoặc giao việc cho agent…"
          aria-label="Câu hỏi"
          className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-reading placeholder:text-muted-foreground focus-visible:shadow-none"
        />
        <Button kind="primary" type="submit">
          Hỏi
        </Button>
      </form>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-da-md">
          {suggestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onAsk(q)}
              className="rounded-lg border border-border px-da-md py-da-xs text-foreground da-body-small transition-colors duration-[120ms] ease-out hover:border-border-strong hover:bg-subtle"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
