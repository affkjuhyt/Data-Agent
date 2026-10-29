import { Tab, TabList, Tabs as AriaTabs } from 'react-aria-components';
import { cn } from '../lib/utils';

/*
 * Figma "Tab" (2015:123) and "Spec · Component · Tab": a 40px bar with a bottom border,
 * the active tab underlined 2px in primary. role=tablist; ←/→ move, Home/End jump.
 * The caller keeps the value in the URL (?tab=…) so a reload lands on the same tab, and draws the
 * panel itself: this is the tab list only (React Aria's Tabs, selection following focus).
 */
interface TabItem<V extends string> {
  value: V;
  label: string;
  count?: number;
}

export function Tabs<V extends string>({
  items,
  value,
  onChange,
  label,
  className,
}: {
  items: TabItem<V>[];
  value: V;
  onChange: (value: V) => void;
  label: string;
  className?: string;
}) {
  return (
    <AriaTabs
      selectedKey={value}
      onSelectionChange={(key) => key !== value && onChange(key as V)}
      className="contents"
    >
      <TabList
        aria-label={label}
        className={cn('flex h-10 items-end border-b border-border', className)}
      >
        {items.map((item) => (
          <Tab
            key={item.value}
            id={item.value}
            className={({ isSelected }) =>
              cn(
                '-mb-px flex cursor-pointer items-center gap-da-xs whitespace-nowrap border-b-2 px-da-lg py-da-md transition-colors duration-[120ms] ease-out',
                isSelected
                  ? 'border-primary text-foreground da-body-strong'
                  : 'border-transparent text-muted-foreground da-body-small hover:text-foreground',
              )
            }
          >
            {item.label}
            {item.count !== undefined && (
              <span className="text-muted-foreground da-caption">{item.count}</span>
            )}
          </Tab>
        ))}
      </TabList>
    </AriaTabs>
  );
}
