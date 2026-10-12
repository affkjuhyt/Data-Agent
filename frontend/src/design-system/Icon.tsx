import { cn } from '../lib/utils';

/*
 * Icons are the SVGs of Figma "DA · Components – Cơ bản" › Icons (node 2014:2), downloaded as-is.
 * Figma recolours an instance by overriding its stroke; we do the same at render time by
 * pointing the default stroke (#17212B, color/text/default) at currentColor.
 * Several glyphs carry a white fill from the vector export (clock, info, eye…); it is kept, but
 * read from --icon-fill so a surface whose Figma instances drop it (the dark Toast) can too.
 */
const ICON_SOURCES = import.meta.glob<string>('./icons/*.svg', {
  eager: true,
  query: '?raw',
  import: 'default',
});

const DEFAULT_STROKE = /stroke="#17212B"/gi;
const WHITE_FILL = /fill="white"/g;

const MARKUP = new Map(
  Object.entries(ICON_SOURCES).map(([path, svg]) => [
    path.replace('./icons/', '').replace('.svg', ''),
    svg
      .replace(DEFAULT_STROKE, 'stroke="currentColor"')
      .replace(WHITE_FILL, 'fill="var(--icon-fill, white)"'),
  ]),
);

export type IconName =
  | 'agent'
  | 'alert'
  | 'api'
  | 'approve'
  | 'arrow-up'
  | 'bell'
  | 'bookmark'
  | 'bucket'
  | 'calendar'
  | 'chart'
  | 'chat'
  | 'check'
  | 'chevron-down'
  | 'chevron-right'
  | 'clock'
  | 'compare'
  | 'copy'
  | 'cpu'
  | 'database'
  | 'directory'
  | 'download'
  | 'edit'
  | 'external-link'
  | 'eye'
  | 'file'
  | 'filter'
  | 'folder'
  | 'globe'
  | 'home'
  | 'info'
  | 'key'
  | 'layers'
  | 'lineage'
  | 'link'
  | 'lock'
  | 'mail'
  | 'message'
  | 'mic'
  | 'more'
  | 'phone'
  | 'play'
  | 'plug'
  | 'plus'
  | 'refresh'
  | 'rollback'
  | 'search'
  | 'server'
  | 'settings'
  | 'sheet'
  | 'shield'
  | 'sliders'
  | 'sparkles'
  | 'stream'
  | 'table'
  | 'trash'
  | 'trending-down'
  | 'trending-up'
  | 'upload'
  | 'users'
  | 'webhook'
  | 'x';

interface IconProps {
  name: IconName;
  /** Figma icons are 16px; the v3.2 visual rules use 14px for secondary icons. */
  size?: 16 | 14 | 12;
  className?: string;
  /** Accessible label; without it the icon is decorative and hidden from screen readers. */
  label?: string;
}

export function Icon({ name, size = 16, className, label }: IconProps) {
  return (
    <span
      className={cn('inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {/* The SVG keeps its 16×16 root; smaller sizes scale the wrapper's content. */}
      <span
        className="block"
        style={size === 16 ? undefined : { transform: `scale(${size / 16})` }}
        // Trusted: build-time files from src/design-system/icons, not user input.
        dangerouslySetInnerHTML={{ __html: MARKUP.get(name) ?? '' }}
      />
    </span>
  );
}
