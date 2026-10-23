/**
 * The first-visit screens against the v1 mock: choosing a Data Space (and asking for one),
 * onboarding's first agent, and the "how it works" guide.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { getMyDataSpaces, getOnboarding } from '../../lib/v1/explore';
import { createHandlers } from '../../mocks/handlers';
import { DataSpacePicker } from './DataSpacePicker';
import HowItWorksPage from './HowItWorksPage';
import { OnboardingPage } from './OnboardingPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('DataSpacePicker', () => {
  it('lists the Data Spaces returned by the V3 membership endpoint and opens one', async () => {
    const onOpen = vi.fn();
    wrap(<DataSpacePicker userName="Đỗ Hải Yến" onOpen={onOpen} onLogout={vi.fn()} />);
    expect(
      await screen.findByRole('heading', { name: 'Chào Hải Yến, chọn Data Space để bắt đầu' }),
    ).toBeInTheDocument();
    expect(await screen.findAllByText('Người phụ trách dữ liệu')).not.toHaveLength(0);
    expect(screen.getAllByText(/0 nguồn · 0 chỉ số · 0 agent.*Chưa dùng/)).not.toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Mở Rủi ro tín dụng' }));
    expect(onOpen).toHaveBeenCalledWith({ id: 'ds-rui-ro-tin-dung', name: 'Rủi ro tín dụng' });
  });

  it('keeps the "open the last one" preference on the server', async () => {
    wrap(<DataSpacePicker userName="Đỗ Hải Yến" onOpen={vi.fn()} onLogout={vi.fn()} />);
    const box = await screen.findByRole('checkbox', {
      name: 'Lần sau mở thẳng Data Space dùng gần nhất',
    });
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    await waitFor(async () => expect((await getMyDataSpaces()).open_last_used).toBe(false));
  });
});

describe('OnboardingPage', () => {
  it('opens on the first agent with the recommended template and starts its test run', async () => {
    const onFinish = vi.fn();
    wrap(<OnboardingPage onFinish={onFinish} />);
    expect(await screen.findByRole('heading', { name: 'Chọn agent đầu tiên' })).toBeInTheDocument();
    expect(
      screen.getByText(/Gói Ngân hàng đã kết nối Core banking, DWH và Sổ cái\./),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Agent Giám sát chỉ số/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );

    fireEvent.click(screen.getByRole('radio', { name: /Agent Báo cáo định kỳ/ }));
    const run = screen.getByRole('button', { name: 'Chạy thử Agent Báo cáo định kỳ' });
    fireEvent.click(run);
    await waitFor(() =>
      expect(onFinish).toHaveBeenCalledWith({
        area: 'agents',
        page: 'RUN',
        id: 'run-onboarding-1',
      }),
    );
  });

  it('goes back to a step drawn elsewhere and records a skip', async () => {
    const onFinish = vi.fn();
    wrap(<OnboardingPage onFinish={onFinish} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Quay lại' }));
    expect(screen.getByRole('heading', { name: 'Kết nối nguồn' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở Kết nối nguồn' }));
    expect(onFinish).toHaveBeenCalledWith({ area: 'data', page: 'CONNECT' });

    fireEvent.click(screen.getByRole('button', { name: 'Bỏ qua, làm sau' }));
    await waitFor(() => expect(onFinish).toHaveBeenLastCalledWith());
    expect((await getOnboarding()).state).toBe('skipped');
  });
});

describe('HowItWorksPage', () => {
  it('shows the journey with the viewer counts and opens each area', async () => {
    wrap(<HowItWorksPage />);
    expect(
      screen.getByRole('heading', {
        name: 'Dữ liệu của bạn, các agent làm việc, bạn ra quyết định',
      }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/nguồn đã kết nối$/)).toBeInTheDocument();
    expect(await screen.findByText(/^148 chỉ số · \d+ thuật ngữ$/)).toBeInTheDocument();
    const steps = screen.getAllByRole('listitem').filter((li) => li.querySelector('button'));
    fireEvent.click(within(steps[2]).getByRole('button'));
    expect(navigate).toHaveBeenCalledWith('analysis', 'EXPLORE');
    fireEvent.click(screen.getByRole('button', { name: 'Bắt đầu: kết nối nguồn đầu tiên' }));
    expect(navigate).toHaveBeenLastCalledWith('data', 'CONNECT');
  });
});
