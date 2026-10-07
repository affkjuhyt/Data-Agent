/**
 * Query keys shared by the V2 screens and the notification inbox. An inbox
 * event invalidates a prefix (`reviewCases.all`), which refreshes every list
 * and detail under it and nothing else.
 */
export const queryKeys = {
  reviewCases: {
    all: ['review-cases'] as const,
    detail: (caseId: string) => ['review-cases', caseId] as const,
  },
  alerts: {
    all: ['alerts'] as const,
  },
  series: {
    detail: (seriesKey: string) => ['series', seriesKey] as const,
  },
};
