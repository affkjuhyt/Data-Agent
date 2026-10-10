/**
 * Scope for the shared answer components (Ask screen and the agent Playground).
 *
 * The components draw with the v3.1 design tokens (design-system/tokens.css). Only the light
 * theme exists there — dark mode is paused in Figma ("zz · Bản tối") — so `mode` is kept for the
 * callers' API but "dark" renders like "light" until the tokens get a dark set.
 */
import React, { createContext, useContext } from 'react';
import { cn } from '../../../lib/utils';

type AnswerThemeMode = 'light' | 'dark';

const Ctx = createContext<AnswerThemeMode>('light');

export const useAnswerTheme = (): AnswerThemeMode => useContext(Ctx);

/** Series colours in the fixed order of color/chart/1..6. */
export const chartColor = (index: number): string => `var(--chart-${(index % 6) + 1})`;

export function AnswerTheme({
  mode,
  children,
  className,
  style,
}: {
  mode: AnswerThemeMode;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <Ctx.Provider value={mode}>
      <div className={cn('text-foreground', className)} style={style}>
        {children}
      </div>
    </Ctx.Provider>
  );
}
