/**
 * Behaviour the Figma dev spec ("DA · Đặc tả cho dev") asks of each component:
 * keyboard, focus, states and the words a screen reader hears.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentChip } from './AgentChip';
import { Avatar } from './Avatar';
import { Badge } from './Badge';
import { Button } from './Button';
import { Checkbox } from './Checkbox';
import { Composer } from './Composer';
import { ConfirmDialog } from './ConfirmDialog';
import { DataFreshness } from './DataFreshness';
import { EmptyState } from './EmptyState';
import { Field } from './Field';
import { Icon } from './Icon';
import { Input } from './Input';
import { KpiCard } from './KpiCard';
import { Menu } from './Menu';
import { NavItem } from './NavItem';
import { Pagination } from './Pagination';
import { Radio } from './Radio';
import { RunStep } from './RunStep';
import { Select } from './Select';
import { SkeletonRows } from './SkeletonRow';
import { StatusDot } from './StatusDot';
import { SuggestionChip } from './SuggestionChip';
import { Tabs } from './Tabs';
import { ToastProvider, useToast } from './Toast';
import { Toggle } from './Toggle';
import { Tooltip } from './Tooltip';

describe('Icon', () => {
  it('draws the Figma glyph with its stroke on currentColor', () => {
    const { container } = render(<Icon name="check" />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.innerHTML).toContain('stroke="currentColor"');
    expect(svg?.innerHTML).not.toContain('#17212B');
  });

  it('is hidden from screen readers unless it has a label', () => {
    const { container, rerender } = render(<Icon name="lock" />);
    expect(container.firstElementChild).toHaveAttribute('aria-hidden', 'true');
    rerender(<Icon name="lock" label="Khoá" />);
    expect(screen.getByRole('img', { name: 'Khoá' })).toBeInTheDocument();
  });
});

describe('Button', () => {
  it('stays focusable but does nothing while disabled', () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Chạy lại
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Chạy lại' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('marks itself busy while loading', () => {
    const onClick = vi.fn();
    render(
      <Button kind="primary" loading onClick={onClick}>
        Lưu
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Lưu' });
    expect(button).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('Tabs', () => {
  type Mode = 'table' | 'sql' | 'chart';
  function Harness() {
    const [value, setValue] = useState<Mode>('table');
    return (
      <Tabs<Mode>
        label="Chế độ"
        value={value}
        onChange={setValue}
        items={[
          { value: 'table', label: 'Bảng' },
          { value: 'sql', label: 'SQL' },
          { value: 'chart', label: 'Biểu đồ', count: 2 },
        ]}
      />
    );
  }

  it('moves with the arrow keys and Home/End', () => {
    render(<Harness />);
    const table = screen.getByRole('tab', { name: 'Bảng' });
    expect(table).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(table, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'SQL' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(screen.getByRole('tab', { name: 'SQL' }), { key: 'End' });
    expect(screen.getByRole('tab', { name: /Biểu đồ/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(screen.getByRole('tab', { name: /Biểu đồ/ }), { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Bảng' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('Select', () => {
  const options = [
    { value: 'v4', label: 'NPL_RATIO v4' },
    { value: 'v5', label: 'NPL_RATIO v5 · nháp' },
  ];

  it('opens, picks with Enter, and keeps the old value on Esc', () => {
    const onChange = vi.fn();
    render(<Select label="Định nghĩa" options={options} value="v4" onChange={onChange} />);
    const combo = screen.getByRole('combobox', { name: 'Định nghĩa' });

    fireEvent.click(combo);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    fireEvent.keyDown(combo, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.click(combo);
    fireEvent.keyDown(combo, { key: 'ArrowDown' });
    fireEvent.keyDown(combo, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('v5');
  });

  it('adds a search box past eight options', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ value: `m${i}`, label: `Chỉ số ${i}` }));
    render(<Select label="Chỉ số" options={many} value={null} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('combobox', { name: 'Chỉ số' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Tìm trong Chỉ số' }), {
      target: { value: '8' },
    });
    expect(screen.getAllByRole('option')).toHaveLength(1);
  });

  it('shows the error under the field', () => {
    render(
      <Select
        label="Định nghĩa"
        options={options}
        value={null}
        onChange={() => {}}
        error="Chọn một định nghĩa trước khi chạy"
      />,
    );
    expect(screen.getByText('Chọn một định nghĩa trước khi chạy')).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('Checkbox, Radio, Toggle', () => {
  it('report the new value', () => {
    const onCheck = vi.fn();
    const onRadio = vi.fn();
    const onToggle = vi.fn();
    render(
      <>
        <Checkbox checked={false} onChange={onCheck}>
          Gửi người phụ trách
        </Checkbox>
        <Radio name="def" value="internal" checked={false} onChange={onRadio}>
          Nội bộ · nhóm 2–5
        </Radio>
        <Toggle label="Bản tin tuần" checked onChange={onToggle} />
      </>,
    );
    fireEvent.click(screen.getByLabelText('Gửi người phụ trách'));
    expect(onCheck).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByLabelText('Nội bộ · nhóm 2–5'));
    expect(onRadio).toHaveBeenCalledWith('internal');
    const toggle = screen.getByRole('switch', { name: 'Bản tin tuần' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});

describe('Menu', () => {
  it('opens from its trigger and runs the chosen item', () => {
    const onSelect = vi.fn();
    render(
      <Menu
        label="Thao tác artifact"
        items={[
          { label: 'Mở toàn màn', icon: 'eye', shortcut: '↵', onSelect },
          { kind: 'separator' },
          { label: 'Xoá artifact', icon: 'x', danger: true, onSelect: () => {} },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Thao tác artifact' }));
    expect(screen.getByRole('separator')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitem', { name: /Mở toàn màn/ }));
    expect(onSelect).toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('timed components', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('Tooltip appears after 300 ms of hover', () => {
    render(
      <Tooltip content="Dữ liệu tới 30/09 23:59 · Core banking">
        <button type="button">Độ mới</button>
      </Tooltip>,
    );
    fireEvent.mouseEnter(screen.getByRole('button').parentElement as HTMLElement);
    act(() => vi.advanceTimersByTime(299));
    expect(screen.queryByRole('tooltip')).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole('tooltip')).toHaveTextContent('Core banking');
  });

  it('Toast closes after 2.5 s, or 5 s when it offers an undo', () => {
    function Trigger() {
      const toast = useToast();
      return (
        <>
          <button type="button" onClick={() => toast({ tone: 'info', message: 'Đã lưu' })}>
            plain
          </button>
          <button
            type="button"
            onClick={() =>
              toast({
                tone: 'success',
                message: 'Đã duyệt',
                action: { label: 'Hoàn tác', onClick: () => {} },
              })
            }
          >
            undo
          </button>
        </>
      );
    }
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('plain'));
    fireEvent.click(screen.getByText('undo'));
    expect(screen.getAllByRole('status')).toHaveLength(2);
    act(() => vi.advanceTimersByTime(2500));
    expect(screen.queryByText('Đã lưu')).toBeNull();
    expect(screen.getByText('Đã duyệt')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(2500));
    expect(screen.queryByText('Đã duyệt')).toBeNull();
  });
});

describe('ConfirmDialog', () => {
  it('focuses Huỷ, and Esc cancels', () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Khoá Agent Đối soát?"
        confirmLabel="Khoá agent"
        onConfirm={() => {}}
        onCancel={onCancel}
      >
        Lượt chạy #427 sẽ dừng an toàn.
      </ConfirmDialog>,
    );
    expect(screen.getByRole('alertdialog', { name: 'Khoá Agent Đối soát?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Huỷ' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' });
    expect(onCancel).toHaveBeenCalled();
  });
});

describe('Pagination', () => {
  it('shows the range and moves pages', () => {
    const onPage = vi.fn();
    render(<Pagination page={1} pageSize={8} total={286} onPage={onPage} />);
    expect(screen.getByText('1–8 / 286')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '1' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '36' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    expect(onPage).toHaveBeenCalledWith(2);
  });
});

describe('Data Agent components', () => {
  it('KpiCard reads the delta as tăng/giảm, not only an arrow', () => {
    render(
      <KpiCard
        label="Biên lợi nhuận gộp · Miền Bắc"
        value="31,2%"
        delta={{ text: '−4,6 điểm so với T8', direction: 'down', good: false }}
        freshness="Dữ liệu đến 30/09 · Sổ cái GL"
      />,
    );
    expect(screen.getByText('giảm', { exact: false })).toHaveClass('sr-only');
    expect(screen.getByText('31,2%')).toBeInTheDocument();
  });

  it('KpiCard without access offers to request it', () => {
    const onRequestAccess = vi.fn();
    render(<KpiCard label="Nợ xấu" state="noAccess" onRequestAccess={onRequestAccess} />);
    expect(screen.getByText('Bạn chưa có quyền xem chỉ số này')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Yêu cầu quyền' }));
    expect(onRequestAccess).toHaveBeenCalled();
  });

  it('AgentChip says its status in words', () => {
    render(<AgentChip name="Agent Phân tích" status="waiting" />);
    expect(screen.getByText('Chờ bạn')).toBeInTheDocument();
  });

  it('RunStep marks the running step as current', () => {
    render(
      <RunStep
        state="running"
        title="Đối chiếu với tài liệu giá"
        detail="Agent tài liệu · đang đọc PL02"
      />,
    );
    expect(screen.getByRole('button')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('…')).toBeInTheDocument();
  });

  it('Composer sends on Enter, not on Shift+Enter, and runs in the background on ⌘Enter', () => {
    const onSubmit = vi.fn();
    render(<Composer placeholder="Hỏi tiếp về phân tích này…" onSubmit={onSubmit} />);
    const field = screen.getByRole('textbox', { name: 'Hỏi tiếp về phân tích này…' });
    fireEvent.change(field, { target: { value: 'Phân tích nhóm này' } });
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: 'Enter', metaKey: true });
    expect(onSubmit).toHaveBeenCalledWith('Phân tích nhóm này', { background: true });
  });

  it('renders the small parts with their words', () => {
    const onSuggest = vi.fn();
    render(
      <>
        <Badge tone="warning">Cần duyệt</Badge>
        <StatusDot tone="success">Chứng nhận</StatusDot>
        <DataFreshness state="stale" label="Dữ liệu cũ · đến 27/09" />
        <Avatar name="Nguyễn Hải Yến" />
        <SuggestionChip label="Phân tích nhóm này" onSelect={onSuggest} />
        <NavItem label="Tổng quan" active />
        <Field label="Tên chỉ số" hint="Viết thường, không dấu">
          <Input placeholder="npl_ratio" />
        </Field>
        <EmptyState title="Không có việc nào cần bạn" description="Các agent đang tự làm việc." />
        <SkeletonRows count={2} />
      </>,
    );
    expect(screen.getByText('Cần duyệt')).toBeInTheDocument();
    expect(screen.getByText('Chứng nhận')).toBeInTheDocument();
    expect(screen.getByText('Dữ liệu cũ · đến 27/09')).toBeInTheDocument();
    expect(screen.getByLabelText('Nguyễn Hải Yến')).toHaveTextContent('HY');
    expect(screen.getByRole('button', { name: 'Tổng quan' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.getByLabelText('Tên chỉ số')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Đang tải…' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Phân tích nhóm này/ }));
    expect(onSuggest).toHaveBeenCalled();
  });
});

describe('Dialog', () => {
  it('is a form: Enter submits, Huỷ and Esc close, a failure keeps it open', async () => {
    const { Dialog } = await import('./Dialog');
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(
      <Dialog open title="Mời người" description="Gửi lời mời" onClose={onClose} onSubmit={onSubmit} submitLabel="Gửi lời mời">
        <input aria-label="Email" />
      </Dialog>,
    );
    expect(screen.getByRole('dialog', { name: 'Mời người' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Email' })).toHaveFocus();
    fireEvent.submit(screen.getByRole('dialog'));
    expect(onSubmit).toHaveBeenCalledOnce();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
    rerender(
      <Dialog open title="Mời người" onClose={onClose} onSubmit={onSubmit} submitLabel="Gửi lời mời" error="Email đã có">
        <input aria-label="Email" />
      </Dialog>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Email đã có');
  });

  it('does not submit while it cannot', async () => {
    const { Dialog } = await import('./Dialog');
    const onSubmit = vi.fn();
    render(
      <Dialog open title="Thêm" onClose={vi.fn()} onSubmit={onSubmit} submitLabel="Thêm" canSubmit={false}>
        <span />
      </Dialog>,
    );
    fireEvent.submit(screen.getByRole('dialog'));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

describe('Drawer', () => {
  it('opens from the right with its title, and Esc or × closes it', async () => {
    const { Drawer } = await import('./Drawer');
    const onClose = vi.fn();
    render(
      <Drawer open title="Kết quả chạy thử" onClose={onClose} footer={<button type="button">Kích hoạt</button>}>
        <p>Nội dung</p>
      </Drawer>,
    );
    expect(screen.getByRole('dialog', { name: 'Kết quả chạy thử' })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe('Textarea', () => {
  it('marks itself invalid and shows the error', async () => {
    const { Textarea } = await import('./Textarea');
    render(<Textarea aria-label="Lý do" error="Ghi lý do" />);
    expect(screen.getByRole('textbox', { name: 'Lý do' })).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Ghi lý do')).toBeInTheDocument();
  });
});
