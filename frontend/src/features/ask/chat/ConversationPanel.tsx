import { useEffect, useState } from 'react';
import { Icon } from '../../../design-system/Icon';
import { listConversations } from '../../../lib/v1/questions';
import { cn } from '../../../lib/utils';

/*
 * The left panel of the chat: a new conversation, a search over every conversation (the api's
 * `q`: filtering the 30 loaded ones said "Không có cuộc trò chuyện khớp" for an older one), and
 * the recent conversations themselves (GET /v1/conversations), newest first.
 */

const SEARCH_DELAY_MS = 250;

const titled = (items: { id: string; title?: string | null }[]): RecentChat[] =>
  items.map((c) => ({ id: c.id, title: c.title?.trim() || 'Hội thoại không tiêu đề' }));

export interface RecentChat {
  id: string;
  title: string;
}

export function useRecentChats(refreshKey: unknown, pageSize = 30): RecentChat[] {
  const [recent, setRecent] = useState<RecentChat[]>([]);
  useEffect(() => {
    let live = true;
    listConversations(pageSize)
      .then((page) => {
        if (live) setRecent(titled(page.items));
      })
      .catch(() => {
        if (live) setRecent([]);
      });
    return () => {
      live = false;
    };
  }, [refreshKey, pageSize]);
  return recent;
}

export function ConversationPanel({
  recent,
  activeId,
  onNew,
  onOpen,
}: {
  recent: RecentChat[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<RecentChat[] | null>(null);
  const term = query.trim();
  useEffect(() => {
    if (!term) {
      setFound(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      listConversations(30, term)
        .then((page) => {
          if (live) setFound(titled(page.items));
        })
        // Without the search, the loaded ones are filtered here rather than showing nothing.
        .catch(() => {
          if (live)
            setFound(recent.filter((c) => c.title.toLowerCase().includes(term.toLowerCase())));
        });
    }, SEARCH_DELAY_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [term, recent]);
  const shown = term ? (found ?? []) : recent;
  return (
    <nav
      aria-label="Cuộc trò chuyện"
      className="flex w-[260px] shrink-0 flex-col gap-da-md border-r border-border bg-subtle/40 px-da-md py-da-lg max-[1023px]:hidden"
    >
      <button
        type="button"
        onClick={onNew}
        className="inline-flex items-center gap-da-sm rounded-md px-da-md py-da-sm text-foreground da-body-strong hover:bg-subtle"
      >
        <Icon name="plus" size={16} /> Cuộc trò chuyện mới
      </button>
      <label className="flex items-center gap-da-sm rounded-md border border-border bg-card px-da-md py-[6px]">
        <Icon name="search" size={14} className="text-muted-foreground" />
        <input
          type="search"
          aria-label="Tìm cuộc trò chuyện"
          placeholder="Tìm cuộc trò chuyện"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-body-small placeholder:text-muted-foreground"
        />
      </label>
      <div className="flex min-h-0 flex-col gap-[2px] overflow-y-auto">
        <span className="px-da-md text-muted-foreground da-overline">Gần đây</span>
        {shown.length === 0 ? (
          <p className="m-0 px-da-md text-muted-foreground da-caption">
            {term
              ? found === null
                ? 'Đang tìm…'
                : 'Không có cuộc trò chuyện khớp.'
              : 'Chưa có cuộc trò chuyện nào.'}
          </p>
        ) : (
          shown.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-current={c.id === activeId ? 'page' : undefined}
              onClick={() => onOpen(c.id)}
              className={cn(
                'flex items-center gap-da-sm truncate rounded-md px-da-md py-da-sm text-left da-body-small',
                c.id === activeId ? 'bg-accent text-primary' : 'text-foreground hover:bg-subtle',
              )}
            >
              <Icon name="chat" size={14} className="shrink-0 text-muted-foreground" />
              <span className="truncate">{c.title}</span>
            </button>
          ))
        )}
      </div>
    </nav>
  );
}
