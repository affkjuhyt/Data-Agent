import { describe, expect, it } from 'vitest';
import { fromAnswer, readRefusal } from './answerMessage';
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
