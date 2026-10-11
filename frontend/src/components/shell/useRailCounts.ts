import { useEffect, useState } from 'react';
import type { AreaId } from './areas';
import { listWorkspaces } from '../../lib/v1/api';
import { listSkills, listSkillSuggestions, listTools } from '../../lib/v1/skills';
import {
  listCollections,
  listErrorGroups,
  listSourceDrifts,
  listSourceRows,
} from '../../lib/v1/sources';
import { listQualityIssues } from '../../lib/v1/documents';
import { listOpenWritebacks, listPipelines } from '../../lib/v1/dataPrep';
import { listDataProducts, listMarts, listQualityRules } from '../../lib/v1/dataProducts';
import {
  listAccessPolicies,
  listAccessRequests,
  listAdminUsers,
  listDataSpaceSummaries,
  listDsr,
} from '../../lib/v1/adminAccess';
import { listIntegrations } from '../../lib/v1/adminPlatform';
import { listAlerts } from '../../lib/v1/alerts';
import { listCrawlSources } from '../../lib/v1/crawl';
import { listCatalogMetrics } from '../../lib/v1/metricCatalog';
import { listMonitors } from '../../lib/v1/monitors';
import { listPackInstalls, listPacks } from '../../lib/v1/packs';
import { listOpenRetroChanges, listPeriods } from '../../lib/v1/periods';
import { listReports } from '../../lib/v1/reports';
import { listGlossary } from '../../lib/v1/semanticLayer';

/*
 * The counts the Figma rails draw beside their items (Thư viện 38, Nguồn 12, Người dùng 412…).
 * Loaded for the open area only, once per visit, from the list each page itself reads; the spec
 * has no totals (A1.4), so a count is the length of the first page of 500, like the overview
 * screens. A list that fails leaves its item without a number rather than a made-up 0.
 */
type Counter = () => Promise<number>;
const count =
  <T>(load: () => Promise<{ items: T[] }>, keep: (item: T) => boolean = () => true): Counter =>
  async () =>
    (await load()).items.filter(keep).length;

const COUNTERS: Partial<Record<AreaId, Record<string, Counter>>> = {
  agents: {
    SKILLS: count(() => listSkills()),
    SKILL_SUGGESTIONS: count(listSkillSuggestions),
    SKILL_VERSIONS: count(
      () => listSkills(),
      (s) => Boolean(s.pending_version_id),
    ),
    TOOLS: count(listTools),
  },
  analysis: {
    SESSIONS: count(listWorkspaces),
    WATCH: count(listMonitors),
    ALERTS: count(() => listAlerts('open')),
    REPORTS: count(listReports),
  },
  data: {
    SOURCES: count(listSourceRows),
    DRIFT: count(listSourceDrifts),
    ERRORS: count(listErrorGroups),
    COLLECTIONS: count(listCollections),
    DOC_QUALITY: count(listQualityIssues),
    PIPELINES: count(listPipelines),
    DATA_MARTS: count(listMarts),
    DATA_PRODUCTS: count(listDataProducts),
    QUALITY: count(listQualityRules),
    WRITEBACK: count(listOpenWritebacks),
    CRAWL: count(listCrawlSources),
    PERIOD: count(listPeriods),
    RETRO: count(listOpenRetroChanges),
    METRIC_CATALOG: count(listCatalogMetrics),
    GLOSSARY: count(listGlossary),
    PACKS: count(listPacks, (p) => p.installed !== null),
    PACK_ONBOARD: count(listPackInstalls),
  },
  admin: {
    USERS: count(listAdminUsers, (u) => u.kind === 'person'),
    DATA_SPACES: count(listDataSpaceSummaries),
    POLICIES: count(listAccessPolicies),
    ACCESS_REQUESTS: count(() => listAccessRequests({ status: 'pending' })),
    INTEGRATIONS: count(listIntegrations),
    PRIVACY: count(listDsr, (d) => d.status !== 'done' && d.status !== 'rejected'),
  },
};

export function useRailCounts(area: AreaId | undefined): Record<string, number> {
  const [counts, setCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    const counters = area ? COUNTERS[area] : undefined;
    setCounts({});
    if (!counters) return;
    let alive = true;
    const entries = Object.entries(counters);
    void Promise.allSettled(entries.map(([, load]) => load())).then((results) => {
      if (!alive) return;
      const next: Record<string, number> = {};
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') next[entries[i][0]] = r.value;
      });
      setCounts(next);
    });
    return () => {
      alive = false;
    };
  }, [area]);
  return counts;
}
