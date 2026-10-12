import type { ReactNode } from 'react';
import { Icon, type IconName } from '../../design-system/Icon';

/* Full-screen card for the states before the app shell: connecting, failed, waiting, set-up. */
export function AuthScreen({
  icon,
  title,
  children,
}: {
  icon: IconName;
  title: string;
  children?: ReactNode;
}) {
  return (
    <main className="flex h-screen items-center justify-center bg-background px-da-xl">
      <div className="flex w-full max-w-md flex-col gap-da-xl rounded-lg border border-border bg-card p-da-3xl shadow-md">
        <div className="flex flex-col items-center gap-da-md text-center">
          <span className="inline-flex size-10 items-center justify-center rounded-md bg-accent text-primary">
            <Icon name={icon} />
          </span>
          <h1 className="text-foreground da-heading-2">{title}</h1>
        </div>
        {children}
      </div>
    </main>
  );
}
