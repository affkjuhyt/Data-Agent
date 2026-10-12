import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { VerifierBadge } from './VerifierBadge';

describe('VerifierBadge', () => {
  it('shows the verdict and each deterministic check', () => {
    render(
      <VerifierBadge
        verification={{
          checks: [{ name: 'numbers_grounded', passed: true, detail: 'Tất cả số đều có nguồn' }],
          verdict: 'PASS',
          iteration: 1,
          confidence: null,
        }}
      />,
    );

    expect(screen.getByTestId('verifier-badge')).toHaveTextContent('Đã kiểm');
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByTestId('verifier-badge')).toHaveTextContent('Tất cả số đều có nguồn');
  });
});
