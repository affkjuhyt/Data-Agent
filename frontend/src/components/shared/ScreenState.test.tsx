import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../../lib/api/errors';
import { parseScreenParams } from '../../lib/urlState';
import { mockFixture } from '../../test-utils/contractFixtures';
import { ScreenState, describeScreenError } from './ScreenState';

describe('ScreenState', () => {
  it('a link the viewer may not open shows "Không có quyền xem" and nothing else (BR-2)', () => {
    const error = new ApiError(403, 'Forbidden', mockFixture('errors/ACCESS_DENIED.json'));
    const { container } = render(<ScreenState object="case" error={error} />);
    expect(screen.getByText('Không có quyền xem')).toBeInTheDocument();
    expect(container).not.toHaveTextContent('role ANALYST');
  });

  it('a link whose parameters are unusable is broken before any request', () => {
    const { invalid } = parseScreenParams('?series=EIA:RBRTE:D&as_of=hôm-qua');
    render(<ScreenState object="series" invalidLink={invalid.length > 0} />);
    expect(screen.getByText('Không tìm thấy chuỗi số liệu trong đường dẫn')).toBeInTheDocument();
  });

  it('an id the server does not know is a broken link, named after what the link points at', () => {
    const error = new ApiError(404, 'Not Found', mockFixture('errors/UNRESOLVED_ENTITY.json'));
    render(<ScreenState object="series" error={error} />);
    expect(screen.getByText('Không tìm thấy chuỗi số liệu trong đường dẫn')).toBeInTheDocument();
    expect(describeScreenError(error, 'alert').message).toBe(
      'Không tìm thấy cảnh báo trong đường dẫn',
    );
  });

  it('other failures show the error with a retry; loading shows the loader; content shows nothing', () => {
    const { rerender, container } = render(
      <ScreenState object="run" error={new ApiError(500, 'Server Error')} onRetry={() => {}} />,
    );
    expect(screen.getByText('Không tải được dữ liệu')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    rerender(<ScreenState object="run" loading />);
    expect(screen.getByText('Đang tải…')).toBeInTheDocument();
    rerender(<ScreenState object="run" />);
    expect(container).toBeEmptyDOMElement();
  });
});
