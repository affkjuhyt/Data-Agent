import type { ReactNode } from 'react';

/** A label above a control. The label is always present (spec: hide it with sr-only, never omit it). */
export function Field({
  label,
  hint,
  hideLabel = false,
  labelTone = 'default',
  children,
}: {
  label: string;
  hint?: string;
  hideLabel?: boolean;
  /** muted = the caption-sized grey label the P04/P05 forms draw; default = DA/Label. */
  labelTone?: 'default' | 'muted';
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-da-xs">
      {/* The hint stays outside <label> so it is not read as part of the field's name. */}
      <label className="flex flex-col gap-da-xs">
        <span
          className={
            hideLabel
              ? 'sr-only'
              : labelTone === 'muted'
                ? 'text-muted-foreground da-caption'
                : 'text-foreground da-label'
          }
        >
          {label}
        </span>
        {children}
      </label>
      {hint && <span className="text-muted-foreground da-caption">{hint}</span>}
    </div>
  );
}
