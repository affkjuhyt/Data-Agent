import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { RadioCard, RadioGroup, RadioOption } from './Radio';

function Gate() {
  const [value, setValue] = useState<'all' | 'exceptions' | 'auto'>('exceptions');
  return (
    <RadioGroup<'all' | 'exceptions' | 'auto'> label="Cổng duyệt" value={value} onChange={setValue}>
      <RadioOption value="all">Duyệt hết</RadioOption>
      <RadioOption value="exceptions">Duyệt ngoại lệ</RadioOption>
      <RadioOption value="auto" disabled>
        Tự động
      </RadioOption>
    </RadioGroup>
  );
}

describe('RadioGroup', () => {
  it('is one named group, one tab stop, moved by the arrow keys past a disabled option', async () => {
    const user = userEvent.setup();
    render(<Gate />);
    expect(screen.getByRole('radiogroup', { name: 'Cổng duyệt' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Duyệt ngoại lệ' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Tự động' })).toBeDisabled();

    await user.tab();
    expect(screen.getByRole('radio', { name: 'Duyệt ngoại lệ' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    // The disabled option is skipped, so the choice wraps to the first one.
    expect(screen.getByRole('radio', { name: 'Duyệt hết' })).toBeChecked();
    await user.tab();
    expect(document.body).toHaveFocus();
  });

  it('picks a card by click, its consequence part of its name', async () => {
    const user = userEvent.setup();
    function Choice() {
      const [value, setValue] = useState<string | null>('a');
      return (
        <RadioGroup label="Phương án" orientation="horizontal" value={value} onChange={setValue}>
          <RadioCard value="a" label="Nhóm 3–5" description="Theo NHNN" />
          <RadioCard value="b" label="Nhóm 2–5" description="Nội bộ" />
        </RadioGroup>
      );
    }
    render(<Choice />);
    await user.click(screen.getByRole('radio', { name: /Nhóm 2–5.*Nội bộ/ }));
    expect(screen.getByRole('radio', { name: /Nhóm 2–5/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Nhóm 3–5/ })).not.toBeChecked();
  });
});
