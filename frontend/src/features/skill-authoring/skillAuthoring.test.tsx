/**
 * P03 skill authoring against the v1 mock: editing and trying a skill, making one from a
 * session, validating it (a failing case blocks review until fixed or noted), running a skill
 * and the external-factor search, and handing a session over to the agent builder.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import { SessionToAgentDialog } from './SessionToAgentDialog';
import SkillEditorPage from './SkillEditorPage';
import SkillFromSessionPage from './SkillFromSessionPage';
import SkillRunPage from './SkillRunPage';
import SkillTestsPage from './SkillTestsPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('SkillEditorPage', () => {
  it('shows the definition and the latest trial, and tries the skill', async () => {
    routeId = 'sk-variance';
    wrap(<SkillEditorPage />);
    expect(await screen.findByText('Phân rã biến động · v4 (nháp)')).toBeInTheDocument();
    expect(screen.getAllByText('metric.query')).toHaveLength(2);
    expect(screen.getByText('v3: 96% · v4: chưa chạy thử')).toBeInTheDocument();
    expect(await screen.findByText(/Mức tăng NPL đóng góp chủ yếu/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Chạy thử' })[1]);
    expect(await screen.findByText('Đã chạy thử v4 · 11 giây')).toBeInTheDocument();
    expect(screen.getByText('v3: 96% · v4: đã chạy thử')).toBeInTheDocument();
  });

  it('saves only after a change, and opens the tests tab as its own screen', async () => {
    routeId = 'sk-variance';
    wrap(<SkillEditorPage />);
    const save = await screen.findByRole('button', { name: 'Lưu phiên bản' });
    expect(save).toHaveAttribute('aria-disabled', 'true');

    const steps = within(screen.getByRole('list', { name: /Các bước/ })).getAllByRole('listitem');
    fireEvent.keyDown(steps[0], { key: 'ArrowDown', altKey: true });
    const reordered = within(screen.getByRole('list', { name: /Các bước/ })).getAllByRole(
      'listitem',
    );
    expect(reordered[0]).toHaveTextContent('Phân rã theo từng chiều được chọn');
    expect(save).not.toHaveAttribute('aria-disabled');
    fireEvent.click(save);
    expect(await screen.findByText('Đã lưu v4 (nháp)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /Kiểm thử/ }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_TESTS', 'sk-variance');
  });

  it('asks before a tab drops unsaved edits', async () => {
    routeId = 'sk-variance';
    wrap(<SkillEditorPage />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Hướng dẫn cho agent' }), {
      target: { value: 'Luôn nêu kỳ so sánh.' },
    });
    fireEvent.click(screen.getByRole('tab', { name: /Phiên bản/ }));
    let dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Bỏ thay đổi chưa lưu?')).toBeInTheDocument();
    expect(within(dialog).getByText('Các thay đổi trong bản nháp sẽ mất.')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Huỷ' }));
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: 'Hướng dẫn cho agent' })).toHaveValue(
      'Luôn nêu kỳ so sánh.',
    );

    fireEvent.click(screen.getByRole('tab', { name: /Phiên bản/ }));
    dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ thay đổi' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_VERSIONS', 'sk-variance');
  });
});

describe('SkillFromSessionPage', () => {
  it('keeps the proposed cells, and saving opens the validation', async () => {
    routeId = 'ws-npl-sept';
    wrap(<SkillFromSessionPage />);
    expect(await screen.findByText('5/7 ô của phiên')).toBeInTheDocument();
    expect(
      screen.getByText('NPL_RATIO → {chỉ số} · T8, T9 → {kỳ trước}, {kỳ này}'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Giữ ô 2' }));
    expect(screen.getByText('4/7 ô của phiên')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Tên' }), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu bản nháp và kiểm định' }));
    expect(await screen.findByText('Đặt tên cho kỹ năng trước khi lưu.')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Tên' }), {
      target: { value: 'Điều tra biến động theo vùng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu bản nháp và kiểm định' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_TESTS', 'sk-region-variance'),
    );
  });

  it('says so when the session has nothing to make a skill from', async () => {
    routeId = 'ws-unknown';
    wrap(<SkillFromSessionPage />);
    expect(await screen.findByText(/Phiên chưa có ô tính nào/)).toBeInTheDocument();
  });
});

describe('SkillTestsPage', () => {
  it('blocks review while a case fails, and applying the fix reruns the suite', async () => {
    routeId = 'sk-region-variance';
    wrap(<SkillTestsPage />);
    expect(await screen.findByText('7/8 đạt')).toBeInTheDocument();
    expect(screen.getByText('Ca “Chỉ số giảm” lỗi')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gửi duyệt' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Áp dụng và chạy lại' }));
    expect(await screen.findByText('8/8 đạt')).toBeInTheDocument();
    expect(screen.getByText('Mọi ca đều đạt')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Gửi duyệt' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_VERSIONS', 'sk-region-variance'),
    );
  });

  it('lets a noted failure go to review, and adds a case', async () => {
    routeId = 'sk-region-variance';
    wrap(<SkillTestsPage />);
    await screen.findByText('7/8 đạt');
    fireEvent.change(screen.getByRole('textbox', { name: /ghi chú cho người duyệt/ }), {
      target: { value: 'Vùng giảm không thuộc phạm vi kỹ năng.' },
    });
    expect(screen.getByRole('button', { name: 'Gửi duyệt' })).not.toHaveAttribute('aria-disabled');

    fireEvent.click(screen.getByRole('button', { name: 'Thêm ca kiểm thử' }));
    const dialog = await screen.findByRole('dialog', { name: 'Thêm ca kiểm thử' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Tên ca' }), {
      target: { value: 'Tiền gửi, giảm' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Kỳ vọng' }), {
      target: { value: 'Tây Nguyên' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm ca' }));
    expect(await screen.findByText('Tiền gửi, giảm')).toBeInTheDocument();
    expect(screen.getByText('8/9 đạt')).toBeInTheDocument();
  });
});

describe('SkillRunPage', () => {
  it('shows the latest decomposition, reruns it and adds it to a session', async () => {
    routeId = 'sk-variance';
    wrap(<SkillRunPage />);
    expect(await screen.findByText(/Nợ xấu bán lẻ tăng 0,22 điểm/)).toBeInTheDocument();
    expect(screen.getByText('Đã chứng nhận')).toBeInTheDocument();
    expect(screen.getByText('DH Bắc Bộ · tiêu dùng không TSBĐ')).toBeInTheDocument();
    expect(screen.getByText('1,84%')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chạy lại' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Thêm vào phiên' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mở phiên' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'SESSIONS', 'ws-npl-sept');

    fireEvent.click(screen.getByRole('link', { name: 'Xem cách làm' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_EDITOR', 'sk-variance');
  });

  it('lists external factors, and says when another metric finds none', async () => {
    routeId = 'sk-external';
    wrap(<SkillRunPage />);
    expect(await screen.findByText('Yếu tố bên ngoài · nợ xấu bán lẻ')).toBeInTheDocument();
    expect(screen.getByText('Lãi suất tiêu dùng và nợ xấu')).toBeInTheDocument();
    expect(screen.getAllByText('Đáng xem')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Giá thép xây dựng' }));
    expect(screen.getByText(/“Giá thép xây dựng” không có ý nghĩa thống kê/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đổi chỉ số' }));
    const dialog = await screen.findByRole('dialog', { name: 'Đổi chỉ số' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Chỉ số' }));
    fireEvent.click(within(dialog).getByRole('option', { name: 'Dư nợ thẻ v2' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tìm lại' }));
    expect(await screen.findByText('Không tìm thấy yếu tố bên ngoài')).toBeInTheDocument();
  });
});

describe('SessionToAgentDialog', () => {
  it('hands the session over to the agent builder as a description', async () => {
    const onClose = vi.fn();
    wrap(<SessionToAgentDialog sessionId="ws-npl-sept" open onClose={onClose} />);
    const dialog = await screen.findByRole('dialog', { name: 'Biến phiên thành agent' });
    expect(await within(dialog).findByText('Ô 3 · SQL phân rã')).toBeInTheDocument();
    expect(
      within(dialog).getByRole('checkbox', { name: 'Ô 1 · Mục tiêu (văn bản)' }),
    ).not.toBeChecked();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo agent nháp' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'BUILDER');
    expect(onClose).toHaveBeenCalled();
    const params = new URLSearchParams(window.location.search);
    expect(params.get('from_session')).toBe('ws-npl-sept');
    expect(params.get('description')).toContain('Ô 3 · SQL phân rã');
    expect(params.get('description')).toContain('Trigger: Thứ Hai 07:00.');
  });

  it('opens the skill form for the same session', async () => {
    wrap(<SessionToAgentDialog sessionId="ws-npl-sept" open onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Lưu thành kỹ năng' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_FROM_SESSION', 'ws-npl-sept');
  });
});
