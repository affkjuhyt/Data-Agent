import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MobileTabBar, mobileTabOf } from './MobileTabBar';

describe('MobileTabBar', () => {
  it('shows the four tabs, marks the current one and counts what needs the viewer', () => {
    const onSelect = vi.fn();
    render(<MobileTabBar active="ask" needsYouCount={4} onSelect={onSelect} />);
    expect(screen.getByRole('button', { name: /Hỏi/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: /Cần bạn/ })).toHaveTextContent('4');
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(onSelect).toHaveBeenCalledWith('agents');
  });

  it('maps pages to tabs: Hỏi đáp is Hỏi, other Phân tích pages have no tab', () => {
    expect(mobileTabOf('analysis', 'ASK')).toBe('ask');
    expect(mobileTabOf('analysis', 'DASHBOARD')).toBeUndefined();
    expect(mobileTabOf('needs-you', undefined)).toBe('needs-you');
    expect(mobileTabOf('data', 'SOURCES')).toBeUndefined();
  });
});
