import { describe, expect, it } from 'vitest';
import { layoutForCommand, responseOrder } from './answerLayout';

describe('answer layouts', () => {
  it('maps chat commands to presentation layouts', () => {
    expect(layoutForCommand('chart')).toBe('chart_first');
    expect(layoutForCommand('sql')).toBe('sql_first');
    expect(layoutForCommand('data')).toBe('data_first');
    expect(layoutForCommand('report')).toBe('report');
  });

  it('changes the order of the primary result sections', () => {
    expect(responseOrder('chart_first', 'chart')).toBeLessThan(responseOrder('chart_first', 'content'));
    expect(responseOrder('sql_first', 'table')).toBeLessThan(responseOrder('sql_first', 'content'));
    // /sql: the SQL that ran comes first, then its result.
    expect(responseOrder('sql_first', 'evidence')).toBeLessThan(responseOrder('sql_first', 'table'));
    expect(responseOrder('report', 'content')).toBeLessThan(responseOrder('report', 'chart'));
  });
});
