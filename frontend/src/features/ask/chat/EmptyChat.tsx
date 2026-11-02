import type { ReactNode } from 'react';
import { Icon } from '../../../design-system/Icon';
import type { RecentChat } from './ConversationPanel';

/** An empty chat: the greeting, the question box, starters from the Data Space's certified
 *  metrics, and the most recent conversations. */
export function EmptyChat({
  name,
  starters,
  recent,
  composer,
  onAsk,
  onOpen,
}: {
  name: string;
  starters: string[];
  recent: RecentChat[];
  composer: ReactNode;
  onAsk: (text: string) => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[760px] flex-col gap-da-xl px-da-xl py-[10vh]">
      <div className="flex flex-col items-center gap-da-sm text-center">
        <span className="inline-flex size-14 items-center justify-center rounded-2xl bg-accent text-primary">
          <Icon name="sparkles" size={16} />
        </span>
        <h2 className="m-0 text-foreground da-heading-2">Xin chào {name}, bạn muốn khám phá dữ liệu nào?</h2>
        <p className="m-0 text-muted-foreground da-body">
          Hỏi bằng lời thường; agent chọn chỉ số, truy vấn và cho bạn xem từng bước nó làm.
        </p>
      </div>
      {composer}
      {starters.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-[2px] p-0" aria-label="Câu hỏi gợi ý">
          {starters.map((q) => (
            <li key={q}>
              <button
                type="button"
                onClick={() => onAsk(q)}
                className="inline-flex w-full items-center gap-da-sm rounded-md px-da-md py-da-sm text-left text-foreground da-body hover:bg-subtle"
              >
                <Icon name="arrow-up" size={14} className="rotate-90 text-muted-foreground" />
                {q}
              </button>
            </li>
          ))}
        </ul>
      )}
      {recent.length > 0 && (
        <section aria-label="Cuộc trò chuyện gần đây" className="flex flex-col gap-da-xs">
          <span className="text-foreground da-body-strong">Cuộc trò chuyện gần đây</span>
          {recent.slice(0, 5).map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => onOpen(c.id)}
              className="inline-flex items-center gap-da-sm truncate rounded-md px-da-md py-da-sm text-left text-foreground da-body-small hover:bg-subtle"
            >
              <Icon name="chat" size={14} className="text-muted-foreground" />
              <span className="truncate">{c.title}</span>
            </button>
          ))}
        </section>
      )}
    </div>
  );
}
