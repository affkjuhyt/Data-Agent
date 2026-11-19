import type { ReactNode } from 'react';
import { Tooltip } from './Tooltip';

/**
 * An action the api answers with 501 until its worker exists: the button inside is disabled by
 * its caller and this says why on hover or focus. With `reason` null (the api serves it) the
 * button is shown as it is, so turning an action on is setting its reason to null.
 */
export function Unavailable({ reason, children }: { reason: string | null; children: ReactNode }) {
  return reason ? <Tooltip content={reason}>{children}</Tooltip> : <>{children}</>;
}
