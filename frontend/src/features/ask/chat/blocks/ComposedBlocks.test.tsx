import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnswerBlockView } from '../../types';
import { ComposedBlocks, isComposed } from './ComposedBlocks';

const blocks = [
  { type: 'markdown', id: 'b1', composed: true, content: 'Europe dẫn đầu doanh thu.' },
  {
    type: 'kpi',
    id: 'b2',
    composed: true,
    items: [{ label: 'Doanh thu', value: 1500, unit: 'USD', change_pct: -12.5, compare_label: 'kỳ trước', trust: 'certified', trust_label: 'Theo chỉ số chuẩn' }],
  },
  { type: 'insights', id: 'b3', composed: true, items: [{ text: 'Pacific giảm mạnh nhất.', tone: 'negative' }] },
  { type: 'timeline_of_the_future', id: 'b4', composed: true, payload: 1 },
] as unknown as AnswerBlockView[];

describe('ComposedBlocks', () => {
  it('draws the blocks in the order the agent chose, an unknown one as its data', () => {
    expect(isComposed(blocks)).toBe(true);
    render(<ComposedBlocks blocks={blocks} />);
    const drawn = screen.getAllByTestId('answer-block').map((el) => el.getAttribute('data-type'));
    expect(drawn).toEqual(['markdown', 'kpi', 'insights', 'timeline_of_the_future']);
    expect(screen.getByText('Europe dẫn đầu doanh thu.')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Doanh thu' })).toHaveTextContent('so với kỳ trước');
    expect(screen.getByText(/timeline_of_the_future/)).toBeInTheDocument();
  });

  it('draws a statistical test apart from the figures, with its method and note (AL-06)', () => {
    const inference = [
      {
        type: 'inference',
        id: 'i1',
        composed: true,
        title: 'Tương quan giá và số đơn',
        method: 'tương quan Pearson',
        columns: ['r', 'p_value', 'n'],
        rows: [{ r: -0.42, p_value: 0.03, n: 26 }],
        row_count: 1,
        note: 'Suy luận thống kê trên mẫu đã truy vấn, không phải số liệu: tương quan không phải nhân quả.',
      },
    ] as unknown as AnswerBlockView[];
    render(<ComposedBlocks blocks={inference} />);
    const block = screen.getByTestId('inference-block');
    expect(block).toHaveTextContent('Suy luận thống kê · tương quan Pearson');
    expect(block).toHaveTextContent('p_value');
    expect(screen.getByRole('note')).toHaveTextContent('tương quan không phải nhân quả');
  });

  it('lists hypotheses with a Check that asks the question, and recommendations (AL-08)', () => {
    const ask = vi.fn();
    const said = [
      {
        type: 'hypothesis',
        id: 'h1',
        composed: true,
        items: [{ text: 'Khách hàng mới giảm ở miền Bắc', check: 'Số khách hàng mới theo vùng tháng 12/2013', status: 'unchecked' }],
      },
      {
        type: 'recommendation',
        id: 'r1',
        composed: true,
        items: [{ action: 'Rà soát kênh bán ở miền Bắc', condition: 'nếu số khách mới tiếp tục giảm', watch: 'Số khách hàng mới' }],
      },
    ] as unknown as AnswerBlockView[];
    render(<ComposedBlocks blocks={said} onAsk={ask} />);
    expect(screen.getByTestId('hypothesis-block')).toHaveTextContent('Giả thuyết · chưa kiểm tra');
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }));
    expect(ask).toHaveBeenCalledWith('Số khách hàng mới theo vùng tháng 12/2013');
    expect(screen.getByTestId('recommendation-block')).toHaveTextContent('theo dõi Số khách hàng mới');
  });

  it('leaves an answer of the older shape to the fixed layout', () => {
    expect(isComposed([{ type: 'markdown', id: 'a-markdown', content: 'x' }] as unknown as AnswerBlockView[])).toBe(false);
  });

  it('offers the chart types the rows fit and redraws on a click', () => {
    const chart = [
      {
        type: 'chart',
        id: 'c1',
        composed: true,
        alternatives: ['line', 'bar'],
        insight: 'Từ 2013-01 đến 2013-02 tăng 100,0%; cao nhất 2013-02, thấp nhất 2013-01.',
        spec: { chart_type: 'line', title: 'Doanh thu', x_key: 'Tháng', y_keys: ['v'], data: [{ Tháng: '2013-01', v: 1 }, { Tháng: '2013-02', v: 2 }] },
      },
    ] as unknown as AnswerBlockView[];
    render(<ComposedBlocks blocks={chart} />);
    // E2: the title once, in the card's header, and the server's one-line reading under it.
    expect(screen.getAllByText('Doanh thu')).toHaveLength(1);
    expect(screen.getByText(/tăng 100,0%; cao nhất 2013-02/)).toBeInTheDocument();
    const group = screen.getByRole('group', { name: 'Kiểu biểu đồ' });
    expect(group).toHaveTextContent('Đường');
    fireEvent.click(screen.getByRole('button', { name: 'Cột' }));
    expect(screen.getByRole('button', { name: 'Cột' })).toHaveAttribute('aria-pressed', 'true');
  });

  // E3: a point of the chart, picked from the keyboard list (a click on the chart picks the same).
  const monthly = [
    {
      type: 'chart',
      id: 'c2',
      composed: true,
      trust: 'certified',
      insight: 'Từ 2013-01 đến 2013-03 tăng 200,0%; cao nhất 2013-03, thấp nhất 2013-01.',
      explore: { metric_id: 'm-1', group_by: ['ngay_dat_hang'], filters: { nam: ['2013'] }, cuts: { Tháng: 'ngay_dat_hang' }, time_cuts: ['ngay_dat_hang'], period: '2013', grain: 'month' },
      spec: {
        chart_type: 'line',
        title: 'Doanh thu theo tháng',
        x_key: 'Tháng',
        y_keys: ['Doanh thu Internet'],
        data: [
          { Tháng: '2013-01', 'Doanh thu Internet': 1 },
          { Tháng: '2013-02', 'Doanh thu Internet': 2 },
          { Tháng: '2013-03', 'Doanh thu Internet': 3 },
        ],
      },
    },
  ] as unknown as AnswerBlockView[];
  const pick = (month: string) =>
    fireEvent.change(screen.getByRole('combobox', { name: 'Chọn điểm theo tháng' }), { target: { value: month } });

  it('asks a follow-up that filters on a point, or asks why it stands out', () => {
    const onAsk = vi.fn();
    render(<ComposedBlocks blocks={monthly} onAsk={onAsk} />);
    pick('2013-02');
    // A month is said as a question says a period, which the agent reads as one.
    fireEvent.click(screen.getByRole('button', { name: /Lọc theo giá trị này/ }));
    expect(onAsk).toHaveBeenLastCalledWith('Doanh thu Internet tháng 2/2013');
    fireEvent.click(screen.getByRole('button', { name: /Phân tích vì sao/ }));
    expect(onAsk).toHaveBeenLastCalledWith('Vì sao Doanh thu Internet tháng 2/2013 tăng so với tháng 1/2013?');
    pick('2013-03');
    fireEvent.click(screen.getByRole('button', { name: /Phân tích vì sao/ }));
    expect(onAsk).toHaveBeenLastCalledWith('Vì sao Doanh thu Internet tháng 3/2013 cao nhất?');
  });

  it('narrows the question the chart answered to a member of a category axis', () => {
    const onAsk = vi.fn();
    const pie = [
      {
        type: 'chart',
        id: 'c3',
        composed: true,
        spec: {
          chart_type: 'pie',
          title: 'Cơ cấu doanh thu',
          x_key: 'Nhóm sản phẩm',
          y_keys: ['Doanh thu'],
          data: [{ 'Nhóm sản phẩm': 'Bikes', 'Doanh thu': 9 }, { 'Nhóm sản phẩm': 'Clothing', 'Doanh thu': 1 }],
        },
      },
    ] as unknown as AnswerBlockView[];
    render(<ComposedBlocks blocks={pie} onAsk={onAsk} question="Cơ cấu doanh thu theo nhóm sản phẩm năm 2013." />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Chọn điểm theo nhóm sản phẩm' }), { target: { value: 'Bikes' } });
    fireEvent.click(screen.getByRole('button', { name: /Lọc theo giá trị này/ }));
    expect(onAsk).toHaveBeenLastCalledWith('Cơ cấu doanh thu theo nhóm sản phẩm năm 2013, chỉ xét nhóm sản phẩm Bikes');
  });

  it('hides a left-out point and its reading until shown again; no follow-up while busy', () => {
    render(<ComposedBlocks blocks={monthly} />);
    pick('2013-02');
    expect(screen.queryByRole('button', { name: /Lọc theo giá trị này/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Loại bỏ điểm này/ }));
    expect(screen.getByText('Đã ẩn 2013-02.')).toBeInTheDocument();
    expect(screen.queryByText(/tăng 200,0%/)).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '2013-02' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hiện lại' }));
    expect(screen.getByText(/tăng 200,0%/)).toBeInTheDocument();
  });

  it('lets a picked point go on a click outside the chart or Esc, and shows every series of it', () => {
    const twoYears = [
      {
        type: 'chart',
        id: 'c4',
        composed: true,
        spec: {
          chart_type: 'bar',
          title: 'Doanh thu theo vùng',
          x_key: 'Vùng',
          y_keys: ['Doanh thu 2012', 'Doanh thu 2013'],
          data: [
            { Vùng: 'France', 'Doanh thu 2012': 2, 'Doanh thu 2013': 4 },
            { Vùng: 'Canada', 'Doanh thu 2012': 5, 'Doanh thu 2013': 6 },
          ],
        },
      },
    ] as unknown as AnswerBlockView[];
    render(
      <div>
        <p>ngoài</p>
        <ComposedBlocks blocks={twoYears} />
      </div>,
    );
    const list = screen.getByRole('combobox', { name: 'Chọn điểm theo vùng' });
    fireEvent.change(list, { target: { value: 'France' } });
    expect(screen.getByRole('toolbar', { name: 'Điểm France' })).toHaveTextContent('Doanh thu 2012 2 · Doanh thu 2013 4');
    fireEvent.mouseDown(screen.getByText('ngoài'));
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
    fireEvent.change(list, { target: { value: 'Canada' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
  });

  it('says a block\'s trust only when the answer mixes trusts', () => {
    const table = (id: string, trust: string, label: string) => ({
      type: 'table', id, composed: true, title: id, columns: ['a'], rows: [{ a: 1 }], row_count: 1, trust, trust_label: label,
    });
    const { unmount } = render(
      <ComposedBlocks blocks={[table('t1', 'certified', 'Theo chỉ số chuẩn'), table('t2', 'certified', 'Theo chỉ số chuẩn')] as never} />,
    );
    // All the same: the answer's own trust line says it once.
    expect(screen.queryByText('Theo chỉ số chuẩn')).not.toBeInTheDocument();
    unmount();
    render(<ComposedBlocks blocks={[table('t1', 'certified', 'Theo chỉ số chuẩn'), table('t2', 'exploratory', 'Khám phá')] as never} />);
    expect(screen.getByText('Theo chỉ số chuẩn')).toBeInTheDocument();
  });

  it('opens a certified reading in Khám phá', () => {
    const onOpenExplore = vi.fn();
    render(<ComposedBlocks blocks={monthly} onOpenExplore={onOpenExplore} />);
    fireEvent.click(screen.getByRole('button', { name: /Mở trong Khám phá/ }));
    expect(onOpenExplore).toHaveBeenCalledWith({
      metric_id: 'm-1',
      group_by: ['ngay_dat_hang'],
      filters: { nam: ['2013'] },
      time_cuts: ['ngay_dat_hang'],
      period: '2013',
      grain: 'month',
    });
  });
});

describe('ComposedBlocks: a block that cannot be drawn', () => {
  it('leaves the rest of the answer readable', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <ComposedBlocks
        blocks={[
          // A composed chart without y_keys: drawing its series throws.
          { type: 'chart', composed: true, spec: { chart_type: 'composed', data: [{ x: 1 }] } },
          { type: 'markdown', composed: true, content: 'Doanh thu quý 3 tăng 12%.' },
        ] as never}
      />,
    );
    expect(screen.getByText('Không hiển thị được phần này của câu trả lời.')).toBeInTheDocument();
    expect(screen.getByText('Doanh thu quý 3 tăng 12%.')).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
