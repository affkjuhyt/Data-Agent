/**
 * The v3.1 frame: top bar areas and counts, the ⌘K palette, the rail and the page header.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AREAS } from './areas';
import { AppTopBar } from './AppTopBar';
import { CommandPalette } from './CommandPalette';
import { PageHeader } from './PageHeader';
import { SideRail } from './SideRail';

const topBar = (props: Partial<Parameters<typeof AppTopBar>[0]> = {}) => (
  <AppTopBar
    areas={AREAS}
    active="home"
    onNavigate={() => {}}
    workspaceName="Rủi ro tín dụng"
    workspaces={[]}
    onWorkspace={() => {}}
    onOpenCommand={() => {}}
    userName="Nguyễn Hải Yến"
    {...props}
  />
);

describe('AppTopBar', () => {
  it('lists the six areas and marks the current one', () => {
    render(topBar({ active: 'agents' }));
    for (const area of AREAS.filter((a) => !a.hidden))
      expect(screen.getAllByText(area.label).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: 'Agents' })).toHaveAttribute('aria-current', 'page');
  });

  it('shows the Cần bạn count only when there is something waiting', () => {
    const { rerender } = render(topBar({ needsYouCount: 4 }));
    expect(screen.getByLabelText('4 việc chờ bạn')).toBeInTheDocument();
    rerender(topBar({ needsYouCount: 0 }));
    expect(screen.queryByLabelText(/việc chờ bạn/)).toBeNull();
  });

  it('hides the running-agents pill until there is a number for it', () => {
    const { rerender } = render(topBar());
    expect(screen.queryByText(/agent đang chạy/)).toBeNull();
    rerender(topBar({ runningAgents: 3 }));
    expect(screen.getByText('3 agent đang chạy')).toBeInTheDocument();
  });

  it('navigates without reloading the page', () => {
    const onNavigate = vi.fn();
    render(topBar({ onNavigate }));
    fireEvent.click(screen.getByRole('link', { name: 'Dữ liệu' }));
    expect(onNavigate).toHaveBeenCalledWith('data');
  });

  it('switches Data Space when there is more than one', () => {
    const onWorkspace = vi.fn();
    render(
      topBar({
        workspaces: [
          { id: 'a', name: 'Rủi ro tín dụng' },
          { id: 'b', name: 'Bán lẻ' },
        ],
        onWorkspace,
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: /Rủi ro tín dụng/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Bán lẻ' }));
    expect(onWorkspace).toHaveBeenCalledWith('b');
  });

  it('checks the current Data Space and closes on Esc, back on its button', async () => {
    const user = userEvent.setup();
    const onWorkspace = vi.fn();
    render(
      topBar({
        workspaces: [
          { id: 'a', name: 'Rủi ro tín dụng' },
          { id: 'b', name: 'Bán lẻ' },
        ],
        onWorkspace,
      }),
    );
    const button = screen.getByRole('button', { name: /Rủi ro tín dụng/ });
    await user.click(button);
    expect(screen.getByRole('menu', { name: 'Data Space: Rủi ro tín dụng' })).toBeInTheDocument();
    expect(screen.getByRole('menuitemradio', { name: 'Rủi ro tín dụng' })).toBeChecked();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    // React Aria hands focus back to the trigger a frame after the popover goes.
    await waitFor(() => expect(button).toHaveFocus());
    expect(onWorkspace).not.toHaveBeenCalled();
  });

  it('folds the other areas into ☰ and navigates from it', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(topBar({ onNavigate }));
    await user.click(screen.getByRole('button', { name: 'Khu vực khác' }));
    const items = screen.getAllByRole('menuitemradio');
    expect(items.length).toBeGreaterThan(0);
    await user.click(items[items.length - 1]);
    expect(onNavigate).toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('CommandPalette', () => {
  const field = () => screen.getByRole('textbox', { name: 'Lệnh' });

  it('filters by name or slash command and runs the selection on Enter', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<CommandPalette open onClose={() => {}} onNavigate={onNavigate} />);
    await user.type(field(), '/duyet');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    await user.keyboard('{Enter}');
    expect(onNavigate).toHaveBeenCalledWith('needs-you', undefined);
  });

  it('finds a screen typed without its marks', async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onClose={() => {}} onNavigate={() => {}} />);
    await user.type(field(), 'trang chu');
    expect(screen.getAllByRole('option').map((o) => o.textContent?.trim())).toEqual(['Trang chủ']);
  });

  it('closes on Esc and says when nothing matches', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} onNavigate={() => {}} />);
    await user.type(field(), 'zzz');
    expect(screen.getByText('Không có lệnh nào khớp “zzz”.')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });
});

describe('SideRail and PageHeader', () => {
  it('marks the active rail item and reports a selection', () => {
    const onSelect = vi.fn();
    render(
      <SideRail
        label="Agents"
        groups={[
          {
            label: 'Agents',
            items: [
              { id: 'TEAM', label: 'Đội agent' },
              { id: 'RUNS', label: 'Lượt chạy' },
            ],
          },
        ]}
        active="TEAM"
        counts={{ TEAM: 7, RUNS: '3 đang chạy' }}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByRole('link', { name: /Đội agent/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('3 đang chạy')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /Lượt chạy/ }));
    expect(onSelect).toHaveBeenCalledWith('RUNS');
  });

  it('renders breadcrumb, title, actions and the line under it', () => {
    render(
      <PageHeader
        breadcrumb={['Agents', 'Đội agent']}
        title="Đội agent"
        description="7 agent · 3 đang chạy"
        actions={<button type="button">Tạo agent</button>}
        bordered
      />,
    );
    expect(screen.getByRole('navigation', { name: 'Đường dẫn' })).toHaveTextContent(
      'Agents / Đội agent',
    );
    expect(screen.getByRole('heading', { level: 1, name: 'Đội agent' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tạo agent' })).toBeInTheDocument();
  });
});

describe('railFor', () => {
  it('leaves out an item whose capability the viewer lacks, and an emptied group', async () => {
    const { railFor } = await import('./AppShell');
    const groups = [
      {
        label: 'Phân tích',
        items: [
          { id: 'ASK', label: 'Hỏi đáp' },
          { id: 'VAULT', label: 'Kho riêng', requires: 'exec_vault' },
        ],
      },
      { label: 'Riêng', items: [{ id: 'ONLY', label: 'Chỉ lãnh đạo', requires: 'exec_vault' }] },
    ];
    expect(railFor(groups, [])?.map((g) => g.items.map((i) => i.id))).toEqual([['ASK']]);
    expect(railFor(groups, ['exec_vault'])?.flatMap((g) => g.items.map((i) => i.id))).toEqual([
      'ASK',
      'VAULT',
      'ONLY',
    ]);
  });
});
