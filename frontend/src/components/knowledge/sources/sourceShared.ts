// Shared, pure (no JSX) helpers for anything that creates or edits a
// KBSource — today that's SourceRegistry.tsx's own Add/Edit forms and
// IngestionWizard.tsx's "From URL" / "From Feed-or-API" / Web Portal
// methods. Kept dependency-free (no React) so either side can import
// just the logic without pulling in the other's component tree.

// 'upload' is bookkeeping-only — a Document Hub/Video/Image upload's row
// (see kb_backend's record_upload_as_source), never something a create
// form itself sends as connector_type.
export type ConnectorType = 'http_file' | 'web_crawl' | 'web_crawl_headless' | 'rss' | 'rest_api' | 'sdmx' | 'upload';

export const CONNECTOR_LABEL: Record<ConnectorType, string> = {
  http_file: 'HTTP File',
  web_crawl: 'Web Crawl',
  web_crawl_headless: 'Web Crawl (headless)',
  rss: 'RSS Feed',
  rest_api: 'REST API',
  sdmx: 'SDMX',
  upload: 'Uploaded File',
};

// Short enough to sit on a pill (full description goes in the `title` tooltip).
export const CONNECTOR_PILL_LABEL: Record<ConnectorType, string> = {
  http_file: 'Remote File',
  web_crawl: 'Web Page',
  web_crawl_headless: 'Web Page (JS)',
  rss: 'RSS Feed',
  rest_api: 'REST API',
  sdmx: 'SDMX',
  upload: 'Uploaded File',
};

export const CONNECTOR_PILL_DESCRIPTION: Record<ConnectorType, string> = {
  http_file: 'PDF/DOCX/XLSX at a direct URL',
  web_crawl: 'Static HTML',
  web_crawl_headless: 'JS-rendered / headless',
  rss: 'RSS feed',
  rest_api: 'REST API with query params + auth',
  sdmx: 'SDMX statistical API',
  upload: 'Uploaded directly from Add Data',
};

// The connector types a "From URL" / "From Feed-or-API" method can create —
// excludes web_crawl/web_crawl_headless (Web Portal owns those) and upload
// (bookkeeping-only, never user-selectable).
export const DOCUMENT_FEED_CONNECTOR_TYPES: ConnectorType[] = ['http_file', 'rss', 'rest_api', 'sdmx'];

/** "key=value" per line -> {key: value}, ignoring blank lines. */
export function parseQueryParams(text: string): Record<string, string> | undefined {
  const entries = text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
    const idx = l.indexOf('=');
    return idx === -1 ? [l, ''] : [l.slice(0, idx).trim(), l.slice(idx + 1).trim()];
  });
  return entries.length ? Object.fromEntries(entries) : undefined;
}

/** The inverse of parseQueryParams — prefills an edit form's textarea. */
export function queryParamsToText(params: Record<string, string> | null | undefined): string {
  return Object.entries(params || {}).map(([k, v]) => `${k}=${v}`).join('\n');
}

export const DAY_LABELS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']; // cron day-of-week: 0=Sun..6=Sat
export const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export type ScheduleMode = 'none' | 'hourly' | 'weekly' | 'monthly' | 'quarterly' | 'yearly';
export const SCHEDULE_MODE_LABEL: Record<ScheduleMode, string> = {
  none: 'No schedule', hourly: 'Hourly',
  weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly', yearly: 'Yearly',
};

export interface ScheduleState {
  mode: ScheduleMode; time: string;
  days: number[];        // weekly: days of week (0=Sun..6=Sat)
  monthDays: number[];   // monthly: one or more days of month (1-31)
  dayOfMonth: number;    // quarterly/yearly: a single day of month
  months: number[];      // yearly: one or more months (1-12)
  hourEvery: number; minuteEvery: number;
}
export const DEFAULT_SCHEDULE_STATE: ScheduleState = {
  mode: 'none', time: '03:00', days: [], monthDays: [1], dayOfMonth: 1, months: [1],
  hourEvery: 0, minuteEvery: 15,
};

/** Compose a 5-field cron from a friendlier picker (iOS Clock-style for
 * weekly) so the person filling the form never has to know cron syntax.
 * `mode: 'none'` returns "" — a manual-only source the worker's cron_due()
 * never auto-triggers (see web_crawler/worker.py's _is_due), only run via
 * "Run Now". Not every real source has a schedule a fixed set of presets can
 * express (a true one-off, or an irregular publication with no fixed
 * cadence) — "no schedule" is a legitimate choice, not a missing feature.
 * Weekly: no days selected, or all 7, both mean "every day" — a wildcard day
 * field. Monthly and yearly both allow picking MULTIPLE days/months (not a
 * separate "twice a month"/"twice a year" mode) — a source that publishes on
 * the 1st and the 15th is still fundamentally "monthly", just on 2 days.
 * `hourly` is one mode covering BOTH "every N minutes" and "every N hours",
 * via 2 fields instead of a separate mode each: `hourEvery = 0` means "don't
 * step by hours, step by minute instead" (`minuteEvery` becomes the minute
 * step interval); `hourEvery > 0` means "every that many hours, at
 * :minuteEvery past the hour" (`minuteEvery` becomes a literal
 * minute-of-hour, `hourEvery` becomes the hour step interval). */
export function buildCron(
  mode: ScheduleMode, time: string, days: number[], monthDays: number[], dayOfMonth: number,
  months: number[], hourEvery: number, minuteEvery: number,
): string {
  if (mode === 'none') return '';
  if (mode === 'hourly') {
    const minute = Math.min(59, Math.max(0, minuteEvery));
    if (hourEvery <= 0) return `*/${Math.min(59, Math.max(1, minute))} * * * *`;
    return `${minute} */${Math.min(23, Math.max(1, hourEvery))} * * *`;
  }
  const [hh, mm] = time.split(':');
  const hour = parseInt(hh, 10) || 0;
  const minute = parseInt(mm, 10) || 0;
  if (mode === 'weekly') {
    const dayField = days.length === 0 || days.length === 7 ? '*' : [...days].sort((a, b) => a - b).join(',');
    return `${minute} ${hour} * * ${dayField}`;
  }
  if (mode === 'monthly') {
    const domField = monthDays.length === 0 ? String(dayOfMonth) : [...monthDays].sort((a, b) => a - b).join(',');
    return `${minute} ${hour} ${domField} * *`;
  }
  if (mode === 'quarterly') return `${minute} ${hour} ${dayOfMonth} 1,4,7,10 *`;
  // yearly
  const monthField = months.length === 0 ? String(months[0] ?? 1) : [...months].sort((a, b) => a - b).join(',');
  return `${minute} ${hour} ${dayOfMonth} ${monthField} *`;
}

/** The inverse of buildCron — reconstructs the picker's state from an
 * existing cron string, so Edit shows the same friendly picker Add uses
 * instead of a raw cron field nobody but a developer can read. Not every
 * cron this app can END UP with is one buildCron itself can PRODUCE (e.g. a
 * hand-edited weekday range like "1-5") — those fall back to
 * DEFAULT_SCHEDULE_STATE ('none'), and isCronRepresentable below is what
 * tells the caller to show a "this came from outside the picker" notice
 * rather than silently discarding it on the next Save. */
export function cronToScheduleState(cron: string | null | undefined): ScheduleState {
  const raw = (cron || '').trim();
  if (!raw) return { ...DEFAULT_SCHEDULE_STATE };
  const parts = raw.split(/\s+/);
  if (parts.length !== 5) return { ...DEFAULT_SCHEDULE_STATE };
  const [min, hour, dom, month, dow] = parts;

  const minStep = min.match(/^\*\/(\d+)$/);
  if (minStep && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'hourly', hourEvery: 0, minuteEvery: parseInt(minStep[1], 10) };
  }
  const hourStep = hour.match(/^\*\/(\d+)$/);
  if (hourStep && /^\d+$/.test(min) && dom === '*' && month === '*' && dow === '*') {
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'hourly', hourEvery: parseInt(hourStep[1], 10), minuteEvery: parseInt(min, 10) };
  }
  // Plain "every hour, at minute X" (e.g. Bulk Import's "hourly" word preset
  // -> "0 * * * *") — a bare wildcard hour, not a "*/N" step. Cron-equivalent
  // to hourEvery=1, so the picker shows "Hourly, every 1 hour" rather than
  // silently resetting to "No schedule" (buildCron writes it back as
  // "X */1 * * *", which every cron reader treats identically to "X * * * *").
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'hourly', hourEvery: 1, minuteEvery: parseInt(min, 10) };
  }
  if (!/^\d+$/.test(min) || !/^\d+$/.test(hour)) return { ...DEFAULT_SCHEDULE_STATE };
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;

  if (dom === '*' && month === '*') {
    if (dow === '*') return { ...DEFAULT_SCHEDULE_STATE, mode: 'weekly', time, days: [] }; // "every day"
    if (/^[\d,]+$/.test(dow)) return { ...DEFAULT_SCHEDULE_STATE, mode: 'weekly', time, days: dow.split(',').map(d => parseInt(d, 10)) };
    return { ...DEFAULT_SCHEDULE_STATE };
  }
  // Monthly: one or more days of month (e.g. "1,15" — Bulk Import's old
  // "semi-monthly" preset is just this with exactly 2 days).
  if (/^\d+(,\d+)*$/.test(dom) && month === '*' && dow === '*') {
    const monthDays = dom.split(',').map(d => parseInt(d, 10)).sort((a, b) => a - b);
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'monthly', time, monthDays, dayOfMonth: monthDays[0] };
  }
  if (/^\d+$/.test(dom) && month === '1,4,7,10' && dow === '*') {
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'quarterly', time, dayOfMonth: parseInt(dom, 10) };
  }
  // Yearly: a single day of month, one or more months (e.g. "1 1,7" — Bulk
  // Import's old "semi-annual" preset is just this with exactly 2 months).
  if (/^\d+$/.test(dom) && /^\d+(,\d+)*$/.test(month) && dow === '*') {
    const months = month.split(',').map(m => parseInt(m, 10)).sort((a, b) => a - b);
    return { ...DEFAULT_SCHEDULE_STATE, mode: 'yearly', time, dayOfMonth: parseInt(dom, 10), months };
  }
  return { ...DEFAULT_SCHEDULE_STATE };
}

/** True when the picker can express `cron` exactly — i.e. round-tripping it
 * through cronToScheduleState -> buildCron reproduces the same string. */
export function isCronRepresentable(cron: string | null | undefined): boolean {
  const raw = (cron || '').trim();
  if (!raw) return true;
  const s = cronToScheduleState(raw);
  return buildCron(s.mode, s.time, s.days, s.monthDays, s.dayOfMonth, s.months, s.hourEvery, s.minuteEvery) === raw;
}

/** The inverse of buildCron, plus the handful of shapes bulk-import's own
 * word->cron presets produce (hourly, semi-monthly, semi-annual) — so the
 * registry list shows "Every year on Jan 1 at 03:00" instead of a raw
 * "0 3 1 1 *" nobody wants to mentally parse. Anything outside these known
 * shapes (a hand-edited or unusual cron) just falls back to the raw string —
 * never guess at a shape this app doesn't itself generate. */
export function cronToHuman(cron: string | null | undefined): string {
  if (!cron || !cron.trim()) return 'No schedule';
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, month, dow] = parts;

  const minStepMatch = min.match(/^\*\/(\d+)$/);
  if (minStepMatch && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return `Every ${minStepMatch[1]} minute${minStepMatch[1] === '1' ? '' : 's'}`;
  }
  const hourStepMatch = hour.match(/^\*\/(\d+)$/);
  if (hourStepMatch && /^\d+$/.test(min) && dom === '*' && month === '*' && dow === '*') {
    return `Every ${hourStepMatch[1]} hour${hourStepMatch[1] === '1' ? '' : 's'} at :${min.padStart(2, '0')}`;
  }
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    return min === '0' ? 'Every hour' : `Every hour at :${min.padStart(2, '0')}`;
  }
  if (!/^\d+$/.test(min) || !/^\d+$/.test(hour)) return cron;
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;

  if (dom === '*' && month === '*') {
    if (dow === '*') return `Every day at ${time}`;
    const days = dow.split(',').map(d => DAY_NAMES[parseInt(d, 10)]?.slice(0, 3) ?? d);
    return `Every ${days.join(', ')} at ${time}`;
  }
  if (month === '*' && dow === '*') {
    const days = dom.split(',').join(', ');
    return `Day${dom.includes(',') ? 's' : ''} ${days} of every month at ${time}`;
  }
  if (dow === '*' && /^[\d,]+$/.test(month)) {
    const months = month.split(',').map(m => MONTH_NAMES[parseInt(m, 10) - 1] ?? m);
    return months.length > 1
      ? `Day ${dom} of ${months.join(', ')} every year at ${time}`
      : `Every year on ${months[0]} ${dom} at ${time}`;
  }
  return cron;
}

// Training/Production/Evaluation — a steward tags data at ingestion time
// (Phase D). Non-production data is fully hidden from RAG/search by default
// (see kb_backend's retrieval filter) — visible only via Data Layers/Source
// Registry directly.
export type DataPurpose = 'training' | 'production' | 'evaluation';
export const DATA_PURPOSE_LABEL: Record<DataPurpose, string> = {
  training: 'Training', production: 'Production', evaluation: 'Evaluation',
};
export const DATA_PURPOSE_DESCRIPTION: Record<DataPurpose, string> = {
  training: 'Used to train or fine-tune a model — never surfaced in search/RAG answers.',
  production: 'The real, verified knowledge base — the only purpose visible in search/RAG answers.',
  evaluation: 'Held out for testing/benchmarking — never surfaced in search/RAG answers.',
};
