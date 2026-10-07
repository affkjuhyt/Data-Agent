/**
 * The states every V2 screen opened from a link has before it has content:
 * loading, no permission, a link that points at nothing, a failure. A link
 * never shows more than the viewer may see — the server decides, this only
 * turns its contract error into the sentence the user reads.
 */
import { Link2Off, Lock } from 'lucide-react';
import { ApiError } from '../../lib/api/errors';
import { EmptyState, ErrorState, LoadingState } from '../ui';

/** What the link points at. */
type ScreenObject = 'series' | 'case' | 'alert' | 'run';

const NOUN: Record<ScreenObject, string> = {
  series: 'chuỗi số liệu',
  case: 'hồ sơ',
  alert: 'cảnh báo',
  run: 'lượt chạy',
};

const FORBIDDEN_MESSAGE = 'Không có quyền xem';
const brokenLinkMessage = (object: ScreenObject) =>
  `Không tìm thấy ${NOUN[object]} trong đường dẫn`;

type ScreenProblem =
  | { kind: 'forbidden'; message: string }
  | { kind: 'broken_link'; message: string }
  | { kind: 'failed'; message: string };

export function describeScreenError(error: unknown, object: ScreenObject): ScreenProblem {
  if (error instanceof ApiError) {
    if (error.status === 403 || error.code === 'ACCESS_DENIED')
      return { kind: 'forbidden', message: FORBIDDEN_MESSAGE };
    if (error.status === 404 || error.code === 'UNRESOLVED_ENTITY')
      return { kind: 'broken_link', message: brokenLinkMessage(object) };
  }
  return { kind: 'failed', message: error instanceof Error ? error.message : String(error) };
}

/**
 * Renders the state, or nothing when the screen has its content. A link with
 * unusable parameters (`invalid` from parseScreenParams) is broken before any
 * request is made.
 */
export function ScreenState({
  object,
  loading,
  error,
  invalidLink,
  onRetry,
}: {
  object: ScreenObject;
  loading?: boolean;
  error?: unknown;
  invalidLink?: boolean;
  onRetry?: () => void;
}) {
  if (invalidLink) return <EmptyState icon={Link2Off} title={brokenLinkMessage(object)} />;
  if (error) {
    const problem = describeScreenError(error, object);
    if (problem.kind === 'forbidden') return <EmptyState icon={Lock} title={problem.message} />;
    if (problem.kind === 'broken_link')
      return <EmptyState icon={Link2Off} title={problem.message} />;
    return <ErrorState message={problem.message} onRetry={onRetry} />;
  }
  if (loading) return <LoadingState />;
  return null;
}
