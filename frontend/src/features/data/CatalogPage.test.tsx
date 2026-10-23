/**
 * R1-04 against the v1 mock: one screen lists the Data Space's tables, documents, series and
 * APIs; a row opens the asset and its source opens what it was taken from.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import CatalogPage from './CatalogPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const kindOf = (row: HTMLElement) => (row as HTMLTableRowElement).cells[0]?.textContent?.trim();
const kinds = (table: HTMLElement) => new Set(within(table).getAllByRole('row').slice(1).map(kindOf));

describe('CatalogPage', () => {
  it('lists tables, documents and series on one screen, each opening its own asset', async () => {
    render(<CatalogPage />);
    const table = await screen.findByRole('table', { name: 'Danh mục' });
    await within(table).findAllByRole('row');
    const seen = kinds(table);
    for (const kind of ['Bảng', 'Cột', 'Tài liệu', 'Chuỗi']) expect(seen).toContain(kind);
    const firstTable = within(table)
      .getAllByRole('row')
      .find((row) => kindOf(row) === 'Bảng')!;
    fireEvent.click(within(firstTable).getAllByRole('button')[0]);
    expect(navigate).toHaveBeenCalledWith('data', 'TABLE', expect.any(String));
  });

  it('opens the source a document was taken from', async () => {
    render(<CatalogPage />);
    const table = await screen.findByRole('table', { name: 'Danh mục' });
    const doc = (await within(table).findAllByRole('row')).find(
      (row) => kindOf(row) === 'Tài liệu',
    )!;
    const buttons = within(doc).getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    expect(navigate).toHaveBeenCalledWith('data', 'COLLECTION', expect.any(String));
  });
});
