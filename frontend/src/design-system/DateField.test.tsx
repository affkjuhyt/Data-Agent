import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { DateField } from './DateField';

function Day({
  initial = '',
  withTime = false,
  max,
}: {
  initial?: string;
  withTime?: boolean;
  max?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <DateField
        label="Ngày nhận"
        value={value}
        onChange={setValue}
        withTime={withTime}
        max={max}
      />
      <output>{value}</output>
    </>
  );
}

const segments = () => screen.getAllByRole('spinbutton');

describe('DateField', () => {
  it('is typed day, month, year — in that order whatever the browser', async () => {
    const user = userEvent.setup();
    render(<Day />);
    const [day, month, year] = segments();
    expect(day).toHaveAccessibleName(/ngày/i);
    expect(month).toHaveAccessibleName(/tháng/i);
    expect(year).toHaveAccessibleName(/năm/i);
    await user.click(day);
    await user.keyboard('20102026');
    expect(screen.getByRole('status')).toHaveTextContent('2026-10-20');
  });

  it('shows a value it is given as dd/mm/yyyy', () => {
    render(<Day initial="2026-03-07" />);
    expect(segments().map((s) => s.textContent)).toEqual(['07', '03', '2026']);
  });

  it('retypes a day it already holds', async () => {
    const user = userEvent.setup();
    // The day goes past the maximum while the month is still November: still typed through.
    render(<Day initial="2026-11-11" max="2026-11-11" />);
    await user.click(segments()[0]);
    await user.keyboard('20102026');
    expect(screen.getByRole('status')).toHaveTextContent('2026-10-20');
  });

  it('empties when it is erased', async () => {
    const user = userEvent.setup();
    render(<Day initial="2026-11-11" />);
    for (const segment of segments()) {
      segment.focus();
      await user.keyboard('{Backspace}{Backspace}{Backspace}{Backspace}');
    }
    expect(screen.getByRole('status')).toHaveTextContent(/^$/);
  });

  it('takes the time too when asked, on a 24-hour clock', async () => {
    const user = userEvent.setup();
    render(<Day withTime />);
    // Vietnamese writes the time first: 18:30 20/10/2026 (CLDR vi-VN).
    await user.click(segments()[0]);
    await user.keyboard('183020102026');
    expect(screen.getByRole('status')).toHaveTextContent('2026-10-20T18:30');
  });
});
