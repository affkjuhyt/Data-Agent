import { useEffect, useRef, type ComponentProps } from 'react';
import type { AskMessage } from '../types';
import { AssistantTurn } from './AssistantTurn';
import { UserMessage } from './UserMessage';

type TurnHandlers = Omit<ComponentProps<typeof AssistantTurn>, 'message' | 'busy' | 'reported'>;

/** The conversation, question then turn, kept scrolled to its newest part. */
export function Thread({
  messages,
  busy,
  handlers,
  reported = {},
}: {
  messages: AskMessage[];
  busy: boolean;
  handlers: TurnHandlers;
  /** Answers the reader reported, by answer id. */
  reported?: Record<string, unknown>;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [messages]);
  return (
    <div className="mx-auto flex w-full max-w-[880px] flex-col gap-da-2xl px-da-xl py-da-2xl">
      {messages.map((m) =>
        m.role === 'user' ? (
          <UserMessage key={m.id} text={m.content} />
        ) : (
          <AssistantTurn
            key={m.id}
            message={m}
            busy={busy}
            reported={!!(m.answerId && reported[m.answerId])}
            {...handlers}
          />
        ),
      )}
      <div ref={bottom} />
    </div>
  );
}
