import { useEffect, useState } from 'react';
import {
  Autocomplete,
  Dialog,
  Header,
  Input,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  Modal,
  ModalOverlay,
  TextField,
} from 'react-aria-components';
import { fold, folds } from '../../lib/fold';
import { cn } from '../../lib/utils';
import { Icon, type IconName } from '../../design-system/Icon';
import type { AreaId } from './areas';

/*
 * "v3.1 · Chi tiết · Command palette" (2072:4148): a 680px panel 120px from the top over a 32%
 * scrim. Input row (agent mark, mono text, ↵ key), a list with Overline group headers and
 * 6×8 rows, a 260px preview on subtle saying what the selected command will do, and a footer
 * of the shortcuts. ↑/↓ move, Enter runs, Esc closes.
 *
 * React Aria's Modal, Dialog and Autocomplete over a ListBox: the focus stays inside while it is
 * open and goes back to what opened it, and the list is driven from the field. Matching ignores
 * case and Vietnamese marks ("trang chu" finds "Trang chủ"); a slash command matches its start.
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
const BY_ID = new Map(COMMANDS.map((c) => [c.id, c]));

// A command matches by its name (any part, marks ignored) or by the start of its slash command.
const matches = (text: string, query: string) => {
  const command = BY_ID.get(text);
  if (!command) return folds(text, query);
  const q = query.trim();
  return !q || folds(command.label, q) || Boolean(command.slash?.startsWith(fold(q)));
};

/** Reports which command has the list's (virtual) focus, so the preview can say what it does. */
function FocusReport({
  id,
  focused,
  onFocus,
}: {
  id: string;
  focused: boolean;
  onFocus: (id: string) => void;
}) {
  useEffect(() => {
    if (focused) onFocus(id);
  }, [focused, id, onFocus]);
  return null;
}

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
  const [focusedId, setFocusedId] = useState<string>(COMMANDS[0].id);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setFocusedId(COMMANDS[0].id);
  }, [open]);

  const shown = COMMANDS.filter((c) => matches(c.id, query));
  const selected = shown.find((c) => c.id === focusedId) ?? shown[0];
  const run = (id: string) => {
    const command = BY_ID.get(id);
    if (command) onNavigate(command.area, command.page);
  };

  return (
    <ModalOverlay
      isOpen={open}
      isDismissable
      onOpenChange={(next) => !next && onClose()}
      className="fixed inset-0 z-[var(--z-dialog)] bg-foreground/[0.32]"
    >
      <Modal className="absolute left-1/2 top-[120px] w-[680px] max-w-[calc(100vw-32px)] -translate-x-1/2">
        <Dialog
          aria-label="Bảng lệnh"
          className="flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-lg outline-none"
        >
          <Autocomplete
            inputValue={query}
            onInputChange={setQuery}
            filter={(text, input) => matches(text, input)}
          >
            <TextField
              aria-label="Lệnh"
              autoFocus
              className="flex items-center gap-da-md border-b border-border px-da-xl py-da-lg"
            >
              <Icon name="agent" className="text-primary" />
              <Input
                placeholder="Gõ lệnh hoặc tên màn hình…"
                className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-mono placeholder:text-muted-foreground focus-visible:shadow-none"
              />
              <kbd className="rounded-sm border border-border px-da-sm py-da-xs text-muted-foreground da-mono">
                ↵
              </kbd>
            </TextField>

            <div className="flex min-h-0">
              <ListBox
                aria-label="Lệnh"
                onAction={(key) => run(String(key))}
                renderEmptyState={() => (
                  <p className="px-da-md py-da-sm text-muted-foreground da-body-small">
                    Không có lệnh nào khớp “{query}”.
                  </p>
                )}
                className="flex max-h-[360px] min-w-0 flex-1 flex-col gap-da-xs overflow-y-auto p-da-md outline-none"
              >
                {GROUPS.map((group) => (
                  <ListBoxSection key={group} id={group} className="flex flex-col gap-da-xs">
                    <Header className="px-da-md uppercase text-muted-foreground da-overline">
                      {group}
                    </Header>
                    {COMMANDS.filter((c) => c.group === group).map((command) => (
                      <ListBoxItem
                        key={command.id}
                        id={command.id}
                        textValue={command.id}
                        className={({ isFocused }) =>
                          cn(
                            'flex cursor-pointer items-center gap-da-md rounded-md px-da-md py-da-sm outline-none',
                            isFocused
                              ? 'bg-subtle text-primary da-body-strong'
                              : 'text-foreground da-body-small',
                          )
                        }
                      >
                        {({ isFocused }) => (
                          <>
                            <FocusReport
                              id={command.id}
                              focused={isFocused}
                              onFocus={setFocusedId}
                            />
                            <Icon
                              name={command.icon}
                              className={isFocused ? 'text-primary' : 'text-muted-foreground'}
                            />
                            <span className="min-w-0 flex-1 truncate">{command.label}</span>
                            {command.slash && (
                              <span className="text-muted-foreground da-mono">{command.slash}</span>
                            )}
                          </>
                        )}
                      </ListBoxItem>
                    ))}
                  </ListBoxSection>
                ))}
              </ListBox>
              {selected && (
                <aside className="flex w-[260px] shrink-0 flex-col gap-da-sm border-l border-border bg-subtle p-da-lg max-[719px]:hidden">
                  <span className="text-muted-foreground da-label">{selected.label}</span>
                  <p className="text-foreground da-body-small">{selected.preview}</p>
                </aside>
              )}
            </div>
          </Autocomplete>

          <footer className="flex gap-da-lg whitespace-nowrap border-t border-border bg-subtle px-da-xl py-da-sm text-muted-foreground da-caption">
            <span>/ lệnh</span>
            <span>↑↓ chọn</span>
            <span>↵ mở</span>
            <span>Esc đóng</span>
          </footer>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
