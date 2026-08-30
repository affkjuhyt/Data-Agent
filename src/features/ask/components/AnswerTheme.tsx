/**
 * Theme scope for the shared answer components.
 *
 * The Playground draws on a near-black panel, the rest of the product on the
 * cream/gold system. The answer block is used in both, so its colours come
 * from CSS variables set by `.answer-theme-light` / `.answer-theme-dark`
 * (src/index.css) rather than being hard-coded per copy of the component.
 */
import React, { createContext, useContext } from 'react';

export type AnswerThemeMode = 'light' | 'dark';

const Ctx = createContext<AnswerThemeMode>('light');

export const useAnswerTheme = (): AnswerThemeMode => useContext(Ctx);

export const CHART_COLORS: Record<AnswerThemeMode, string[]> = {
  dark:  ['#EBD07C', '#93C5FD', '#86EFAC', '#F8A4A4', '#C084FC', '#7DD3FC'],
  light: ['#B88719', '#0E5F63', '#1D6F4C', '#A83A32', '#6B4FBB', '#2F6FB3'],
};

export const CITATION_COLORS: Record<AnswerThemeMode, Record<string, string>> = {
  dark:  { kb: '#93C5FD', web: '#86EFAC', mcp: '#C084FC', tool: '#86EFAC', db: '#93C5FD' },
  light: { kb: '#2F6FB3', web: '#1D6F4C', mcp: '#6B4FBB', tool: '#1D6F4C', db: '#0E5F63' },
};

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
      <div className={`answer-theme-${mode}${className ? ` ${className}` : ''}`} style={style}>
        {children}
      </div>
    </Ctx.Provider>
  );
}
