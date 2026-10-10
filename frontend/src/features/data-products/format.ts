import type { DataProduct } from '../../lib/v1/dataProducts';

/** "sales_margin · v2.3 → v3": the code, the current version and a draft on top of it. */
export const versionLabel = (p: DataProduct) =>
  `${p.code} · v${p.version}${p.next_version ? ` → v${p.next_version}` : ''}`;
