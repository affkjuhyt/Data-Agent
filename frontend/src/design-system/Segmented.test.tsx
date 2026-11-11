import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Segmented } from './Segmented';

function Filter({ variant }: { variant?: 'track' | 'outline' | 'chips' }) {
  const [value, setValue] = useState<'open' | 'soon' | 'done'>('open');
  return (
    <>
      <Segmented<'open' | 'soon' | 'done'>
        label="Lọc yêu cầu"
        value={value}
        onChange={setValue}
        variant={variant}
        items={[
          { value: 'open', label: 'Đang chờ' },
          { value: 'soon', label: 'Sắp hết hạn' },
          { value: 'done', label: 'Đã xử lý' },
        ]}
      />
      <output>{value}</output>
    </>
  );
}

describe('Segmented', () => {
  it.each(['track', 'outline', 'chips'] as const)('%s: a click chooses one', (variant) => {
    render(<Filter variant={variant} />);
    expect(screen.getByRole('radiogroup', { name: 'Lọc yêu cầu' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Đã xử lý' }));
    expect(screen.getByRole('status')).toHaveTextContent('done');
    expect(screen.getByRole('radio', { name: 'Đã xử lý' })).toBeChecked();
  });

  it('is one tab stop, and the arrow keys move the choice (ARIA radio group)', () => {
    render(<Filter />);
    const radios = screen.getAllByRole('radio');
    // Native radios of one name are a single tab stop; the hand-made buttons were three.
    expect(new Set(radios.map((r) => r.getAttribute('name'))).size).toBe(1);
    expect(radios[0].getAttribute('name')).toBeTruthy();
    radios[0].focus();
    fireEvent.keyDown(radios[0], { key: 'ArrowRight' });
    expect(screen.getByRole('status')).toHaveTextContent('soon');
  });
});
