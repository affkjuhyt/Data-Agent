/**
 * Phân tích against the v1 mock: the ways to start lead to their screens, and with no session
 * yet the page shows the designed empty state.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import AnalysisPage from './AnalysisPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

describe('AnalysisPage', () => {
  it('opens Khám phá and Báo cáo định kỳ from their cards', async () => {
    render(<AnalysisPage />);
    fireEvent.click(await screen.findByRole('button', { name: /Khám phá kéo thả/ }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'EXPLORE');
    fireEvent.click(screen.getByRole('button', { name: /Báo cáo định kỳ/ }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'REPORTS');
  });

  it('shows the empty state when there is no session yet', async () => {
    server.use(
      http.get('*/v1/workspaces', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    render(<AnalysisPage />);
    expect(await screen.findByText('Chưa có dashboard hay phiên nào')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở Khám phá' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'EXPLORE');
  });
});
