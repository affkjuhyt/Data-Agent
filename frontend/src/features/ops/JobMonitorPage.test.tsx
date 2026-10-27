import { render, screen } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import JobMonitorPage from './JobMonitorPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
});
afterAll(() => server.close());

describe('JobMonitorPage', () => {
  it('lists ingestion jobs read-only with status', async () => {
    render(<JobMonitorPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Công việc' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Tạm dừng|Chạy lại|Tiếp tục/ })).toBeNull();
    expect(screen.getByRole('link', { name: 'Mở Job nạp' })).toBeInTheDocument();
    // Seeded sources mock always has at least one job row.
    expect(await screen.findByRole('table', { name: 'Danh sách job nạp' })).toBeInTheDocument();
  });
});
