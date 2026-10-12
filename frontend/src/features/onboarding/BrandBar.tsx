import type { ReactNode } from 'react';
import { Icon } from '../../design-system/Icon';

/*
 * The top line of the full screens before the app shell (Onboarding 2076:5585, Chọn Data Space
 * 2230:14988): the 28px mark and "Data Agent" on the left, one quiet action on the right.
 */
export function BrandBar({ children }: { children?: ReactNode }) {
  return (
    <div className="flex w-full items-center gap-da-md px-da-xl py-da-lg">
      <span className="inline-flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <Icon name="agent" />
      </span>
      <span className="text-foreground da-heading-3">Data Agent</span>
      <span className="flex-1" />
      {children}
    </div>
  );
}
