import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { BehaviorPolicy } from '../../lib/v1/types';
import { RulesPanel } from './RulesPanel';

const rule = (id: string, applied: number | null): BehaviorPolicy => ({
  id,
  scope: 'user',
  kind: 'auto_handle',
  description: `Quy tắc ${id}`,
  origin: 'hitl',
  status: 'active',
  applied_this_week: applied,
});

const state = (items: BehaviorPolicy[]) => ({
  data: { items, next_page_token: '' },
  error: undefined,
  loading: false,
  reload: () => {},
});

describe('RulesPanel', () => {
  it('counts the rules, not handled requests, while the api does not count their uses', () => {
    render(<RulesPanel rules={state([rule('a', null), rule('b', null)])} />);
    expect(screen.getByText('2 quy tắc')).toBeInTheDocument();
    expect(screen.queryByText(/việc$/)).toBeNull();
    expect(screen.queryByText(/lần$/)).toBeNull();
  });

  it('adds up what the rules handled this week when it is counted', () => {
    render(<RulesPanel rules={state([rule('a', 3), rule('b', 1)])} />);
    expect(screen.getByText('4 việc')).toBeInTheDocument();
    expect(screen.getByText('3 lần')).toBeInTheDocument();
  });
});
