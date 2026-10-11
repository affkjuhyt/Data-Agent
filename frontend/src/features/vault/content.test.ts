import { describe, expect, it } from 'vitest';
import { proposeCondition } from './content';

describe('proposeCondition', () => {
  it('takes the number after the direction word, not a date before it', () => {
    expect(proposeCondition('Giá xăng E5 kỳ 22/10 sẽ trên 20 nghìn')).toEqual({
      comparator: 'gte',
      threshold: 20000,
    });
    expect(proposeCondition('Lãi suất 12T tuần 45 không quá 5,0%')).toEqual({
      comparator: 'lte',
      threshold: 5,
      unit: '%',
    });
    expect(proposeCondition('Giá trần ≥ 20.000 đ/lít')).toEqual({
      comparator: 'gte',
      threshold: 20000,
    });
    expect(proposeCondition('Sẽ tăng')).toEqual({});
  });
});
