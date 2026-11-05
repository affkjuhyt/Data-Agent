import type { ReactNode } from 'react';
import { AccessDenied, forbiddenOf } from '../features/access/AccessDenied';
import { Button } from './Button';
import { Notice } from './Notice';
import { SkeletonRows } from './SkeletonRow';
import { useDelayedFlag } from './useDelayedFlag';

/*
 * The data states of one region, as "Mẫu tương tác" sets them: under 300 ms nothing, then a
 * skeleton; a load error is a banner with "Thử lại" inside the region while the rest of the
 * page keeps working; no data hands over to `empty`. Data already shown stays during a reload.
 * A 403 that carries the access block (UC-11-07) shows Thiếu quyền instead of the load banner,
 * so screens that forget forbiddenOf still match the designed refusal.
 */
export function AsyncRegion<T>({
  state,
  what,
  skeletonRows = 3,
  isEmpty,
  empty,
  children,
}: {
  state: { data: T | undefined; error: Error | undefined; loading: boolean; reload: () => void };
  /** What failed to load, for the error sentence: "việc cần bạn", "đội agent". */
  what: string;
  skeletonRows?: number;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  const showSkeleton = useDelayedFlag(state.loading && state.data === undefined);
  if (state.data === undefined) {
    if (state.error) {
      const access = forbiddenOf(state.error);
      if (access) return <AccessDenied access={access} />;
      return (
        <Notice
          tone="error"
          action={
            <Button size="sm" onClick={state.reload}>
              Thử lại
            </Button>
          }
        >
          Không tải được {what}. {state.error.message}
        </Notice>
      );
    }
    return showSkeleton ? <SkeletonRows count={skeletonRows} label={`Đang tải ${what}…`} /> : null;
  }
  if (isEmpty?.(state.data) && empty) return <>{empty}</>;
  return <>{children(state.data)}</>;
}
