/**
 * The v3.1 frame: top bar areas and counts, the ⌘K palette, the rail and the page header.
 */
import { fireEvent, render, screen } from '@testing-library/react';
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
    fireEvent.click(screen.getByRole('option', { name: 'Bán lẻ' }));
    expect(onWorkspace).toHaveBeenCalledWith('b');
  });
});

describe('CommandPalette', () => {
  it('filters by name or slash command and runs the selection on Enter', () => {
    const onNavigate = vi.fn();
    render(<CommandPalette open onClose={() => {}} onNavigate={onNavigate} />);
    const input = screen.getByRole('combobox', { name: 'Lệnh' });
    fireEvent.change(input, { target: { value: '/duyet' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onNavigate).toHaveBeenCalledWith('needs-you', undefined);
  });

  it('closes on Esc and says when nothing matches', () => {
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} onNavigate={() => {}} />);
    const input = screen.getByRole('combobox', { name: 'Lệnh' });
    fireEvent.change(input, { target: { value: 'zzz' } });
    expect(screen.getByText('Không có lệnh nào khớp “zzz”.')).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Escape' });
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
