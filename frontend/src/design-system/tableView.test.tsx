import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useTableView, SortableTh, TablePager, TableToolbar } from './tableView';

type Row = { name: string; kind: string; rows: number };

const ROWS: Row[] = [
  { name: 'forex', kind: 'table', rows: 30 },
  { name: 'countries', kind: 'table', rows: 2 },
  { name: 'rates', kind: 'view', rows: 1 },
];

function Harness({ pageSize = 2 }: { pageSize?: number }) {
  const view = useTableView(ROWS, {
    search: (row) => `${row.name} ${row.kind}`,
    filters: [{ id: 'kind', label: 'Loại', value: (row) => row.kind }],
    sorts: [
      { id: 'name', value: (row) => row.name },
      { id: 'rows', value: (row) => row.rows },
    ],
    pageSize,
  });
  return (
    <div>
      <TableToolbar view={view} placeholder="Tìm bảng" searchLabel="Tìm bảng" />
      <table>
        <thead>
          <tr>
            <SortableTh view={view} column="name">
              Bảng
            </SortableTh>
            <SortableTh view={view} column="rows">
              Dòng
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}

describe('table view', () => {
  it('searches, filters, sorts and pages the same rows', () => {
    render(<Harness />);
    expect(screen.getByText('1–2 / 3')).toBeInTheDocument();
    expect(screen.getByText('forex')).toBeInTheDocument();
    expect(screen.queryByText('rates')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    expect(screen.getByText('rates')).toBeInTheDocument();
    expect(screen.queryByText('forex')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Tìm bảng'), { target: { value: 'count' } });
    expect(screen.getByText('countries')).toBeInTheDocument();
    expect(screen.queryByText('forex')).not.toBeInTheDocument();
    expect(screen.getByText('1–1 / 1')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Tìm bảng'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lọc theo Loại' }));
    fireEvent.click(screen.getByRole('option', { name: 'Loại: view' }));
    expect(screen.getByText('rates')).toBeInTheDocument();
    expect(screen.queryByText('forex')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Lọc theo Loại' }));
    fireEvent.click(screen.getByRole('option', { name: 'Loại: tất cả' }));
    fireEvent.click(screen.getByRole('columnheader', { name: /Dòng/ }));
    expect(screen.getByRole('columnheader', { name: /Dòng/ })).toHaveAttribute('aria-sort', 'ascending');
    const names = screen.getAllByRole('row').slice(1).map((row) => row.textContent);
    expect(names).toEqual(['rates', 'countries']);
  });
});
