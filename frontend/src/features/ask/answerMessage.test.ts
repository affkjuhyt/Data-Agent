import { describe, expect, it } from 'vitest';
import { citationsOf, fromAnswer, readRefusal } from './answerMessage';
import type { Answer } from '../../lib/v1/questions';

describe('readRefusal', () => {
  it('R0-7 draws the code and the next action the worker kept with the refusal', () => {
    const refusal = readRefusal({
      text: 'Không có dữ liệu trong khoảng thời gian được hỏi.',
      number_check: { refusal: { code: 'NO_DATA_FOR_PERIOD', next_action: 'rephrase' } },
    });
    expect(refusal).toEqual({
      code: 'NO_DATA_FOR_PERIOD',
      message: 'Không có dữ liệu trong khoảng thời gian được hỏi.',
      next_action: 'rephrase',
    });
  });

  it('keeps an answer from before the code, or of another shape, to its message', () => {
    expect(readRefusal({ text: 'Chưa trả lời được.', number_check: {} })).toEqual({
      code: 'REFUSED',
      message: 'Chưa trả lời được.',
      next_action: '',
    });
    // An action the screen does not know renders as none, never as a wrong one.
    const odd = readRefusal({
      text: 'x',
      number_check: { refusal: { code: 'ACCESS_DENIED', next_action: 'delete_everything' } },
    });
    expect(odd.next_action).toBe('');
  });

  it('is what a refused answer becomes', () => {
    const drawn = fromAnswer({
      kind: 'refusal',
      text: 'Bạn chưa có quyền xem dữ liệu này.',
      number_check: { refusal: { code: 'ACCESS_DENIED', next_action: 'contact_steward' } },
    } as unknown as Answer);
    expect(drawn.refusal?.code).toBe('ACCESS_DENIED');
    expect(drawn.refusal?.next_action).toBe('contact_steward');
  });
});

describe('a citation to a table cell (DI-12)', () => {
  it('opens the cell’s page with its box', () => {
    const [c] = citationsOf({
      citations: [
        {
          n: 1,
          kind: 'table_cell',
          document_id: 'd1',
          document_title: 'Báo cáo dư nợ',
          doc_no: null,
          section_path: null,
          page_from: 1,
          superseded_by: null,
          quote: 'Hà Nội - Năm 2024 > Thực hiện: 1.300',
          cell: { page: 2, bbox: { l: 260, t: 240, r: 340, b: 260 } },
        },
      ],
    } as never);
    expect(c.page).toBe(2);
    expect(c.box).toEqual({ x: 260, y: 240, w: 80, h: 20 });
  });

  it('keeps a passage citation as it was', () => {
    const [c] = citationsOf({
      citations: [
        {
          n: 1,
          document_id: 'd1',
          document_title: 'Quy chế',
          doc_no: null,
          section_path: null,
          page_from: 3,
          superseded_by: null,
          quote: 'x',
        },
      ],
    } as never);
    expect([c.page, c.box]).toEqual([3, undefined]);
  });
});

describe('the page a citation opens', () => {
  it('hands the page and the cell’s box once', async () => {
    const { requestPage, takeFocus, takePage } = await import('../documents/focusPage');
    requestPage('doc', 2, { x: 1, y: 2, w: 3, h: 4 });
    expect(takeFocus('doc')).toEqual({ page: 2, box: { x: 1, y: 2, w: 3, h: 4 } });
    expect(takeFocus('doc')).toBeUndefined();
    requestPage('doc', 5);
    expect(takePage('doc')).toBe(5);
  });
});
