import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { cn } from '../../lib/utils';
import { Icon, type IconName } from '../../design-system/Icon';
import type { AreaId } from './areas';

/*
 * "v3.1 · Chi tiết · Command palette" (2072:4148): a 680px panel 120px from the top over a 32%
 * scrim. Input row (agent mark, mono text, ↵ key), a list with Overline group headers and
 * 6×8 rows, a 260px preview on subtle saying what the selected command will do, and a footer
 * of the shortcuts. ↑/↓ move, Enter runs, Esc closes.
 *
 * Only commands that land on a working screen are listed; "/giao" (hand a task to an agent)
 * arrives with the agent task API.
 */
interface Command {
  id: string;
  group: 'Lệnh' | 'Đi tới';
  label: string;
  icon: IconName;
  slash?: string;
  preview: string;
  area: AreaId;
  page?: string;
}

const COMMANDS: Command[] = [
  {
    id: 'ask',
    group: 'Lệnh',
    label: 'Hỏi trong phiên mới',
    icon: 'chat',
    slash: '/hoi',
    preview: 'Mở Hỏi đáp; agent trả lời bằng số liệu đã chứng nhận và hỏi lại khi câu hỏi mơ hồ.',
    area: 'analysis',
    page: 'ASK',
  },
  {
    id: 'review',
    group: 'Lệnh',
    label: 'Mở yêu cầu cần bạn',
    icon: 'approve',
    slash: '/duyet',
    preview: 'Mở các việc agent đang chờ bạn quyết định.',
    area: 'needs-you',
  },
  {
    id: 'agent',
    group: 'Lệnh',
    label: 'Mở đội agent',
    icon: 'agent',
    slash: '/agent',
    preview: 'Xem agent nào đang chạy, đang chờ, hoặc lỗi.',
    area: 'agents',
    page: 'TEAM',
  },
  {
    id: 'go-home',
    group: 'Đi tới',
    label: 'Trang chủ',
    icon: 'home',
    preview: 'Việc cần bạn, agent đang làm gì và dữ liệu hôm nay.',
    area: 'home',
  },
  {
    id: 'go-runs',
    group: 'Đi tới',
    label: 'Lượt chạy',
    icon: 'play',
    preview: 'Từng lượt chạy của agent: các bước, thời gian, lỗi.',
    area: 'agents',
    page: 'RUNS',
  },
  {
    id: 'go-data',
    group: 'Đi tới',
    label: 'Dữ liệu',
    icon: 'database',
    preview: 'Nguồn đã kết nối, dữ liệu đã chuẩn bị và định nghĩa nghiệp vụ.',
    area: 'data',
  },
  {
    id: 'go-admin',
    group: 'Đi tới',
    label: 'Quản trị',
    icon: 'settings',
    preview: 'Người dùng, quyền, mô hình AI và nhật ký.',
    area: 'admin',
  },
];

const GROUPS: Command['group'][] = ['Lệnh', 'Đi tới'];

export function CommandPalette({
  open,
  onClose,
  onNavigate,
}: {
  open: boolean;
  onClose: () => void;
  onNavigate: (area: AreaId, page?: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActive(0);
    inputRef.current?.focus();
  }, [open]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter((c) => c.label.toLowerCase().includes(q) || c.slash?.startsWith(q));
  }, [query]);

  if (!open) return null;

  const run = (command: Command | undefined) => command && onNavigate(command.area, command.page);
  const selected = matches[active];

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      run(selected);
    }
  };

  return (
    <div className="fixed inset-0 z-[var(--z-dialog)]" onKeyDown={onKeyDown}>
      <button
        type="button"
        aria-label="Đóng bảng lệnh"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 bg-foreground opacity-[0.32]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Bảng lệnh"
        className="absolute left-1/2 top-[120px] flex w-[680px] max-w-[calc(100vw-32px)] -translate-x-1/2 flex-col overflow-hidden rounded-lg border border-border bg-card shadow-lg"
      >
        <div className="flex items-center gap-da-md border-b border-border px-da-xl py-da-lg">
          <Icon name="agent" className="text-primary" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            placeholder="Gõ lệnh hoặc tên màn hình…"
            aria-label="Lệnh"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={selected ? `command-${selected.id}` : undefined}
            className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-mono placeholder:text-muted-foreground focus-visible:shadow-none"
          />
          <kbd className="rounded-sm border border-border px-da-sm py-da-xs text-muted-foreground da-mono">
            ↵
          </kbd>
        </div>

        <div className="flex min-h-0">
          <ul
            id="command-palette-list"
            role="listbox"
            aria-label="Lệnh"
            className="flex max-h-[360px] min-w-0 flex-1 flex-col gap-da-xs overflow-y-auto p-da-md"
          >
            {GROUPS.map((group) => {
              const items = matches.filter((c) => c.group === group);
              if (items.length === 0) return null;
              return (
                <li key={group} role="presentation" className="flex flex-col gap-da-xs">
                  <span className="px-da-md uppercase text-muted-foreground da-overline">
                    {group}
                  </span>
                  <ul role="presentation" className="flex flex-col">
                    {items.map((command) => {
                      const index = matches.indexOf(command);
                      const isActive = index === active;
                      return (
                        <li
                          key={command.id}
                          id={`command-${command.id}`}
                          role="option"
                          aria-selected={isActive}
                          onMouseEnter={() => setActive(index)}
                          onClick={() => run(command)}
                          className={cn(
                            'flex cursor-pointer items-center gap-da-md rounded-md px-da-md py-da-sm',
                            isActive
                              ? 'bg-subtle text-primary da-body-strong'
                              : 'text-foreground da-body-small',
                          )}
                        >
                          <Icon
                            name={command.icon}
                            className={isActive ? 'text-primary' : 'text-muted-foreground'}
                          />
                          <span className="min-w-0 flex-1 truncate">{command.label}</span>
                          {command.slash && (
                            <span className="text-muted-foreground da-mono">{command.slash}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </li>
              );
            })}
            {matches.length === 0 && (
              <li className="px-da-md py-da-sm text-muted-foreground da-body-small">
                Không có lệnh nào khớp “{query}”.
              </li>
            )}
          </ul>
          {selected && (
            <aside className="flex w-[260px] shrink-0 flex-col gap-da-sm border-l border-border bg-subtle p-da-lg max-[719px]:hidden">
              <span className="text-muted-foreground da-label">{selected.label}</span>
              <p className="text-foreground da-body-small">{selected.preview}</p>
            </aside>
          )}
        </div>

        <footer className="flex gap-da-lg whitespace-nowrap border-t border-border bg-subtle px-da-xl py-da-sm text-muted-foreground da-caption">
          <span>/ lệnh</span>
          <span>↑↓ chọn</span>
          <span>↵ mở</span>
          <span>Esc đóng</span>
        </footer>
      </div>
    </div>
  );
}
