/**
 * LLM spend, from workflow_runtime behind Kong:
 *   GET /api/billing/spend?days=30 →
 *     { currency, available, total, series:[{date, spend, tokens}], by_model:[{model, spend, tokens}], note }
 * `available=false` means the runtime has no spend source configured; the
 * `note` explains why and the UI shows it in an EmptyState.
 */
import { useCallback, useEffect, useState } from 'react';
import { apiGet } from './apiClient';

export interface SpendPoint { date: string; spend: number; tokens: number }
export interface SpendByModel { model: string; spend: number; tokens: number }
interface BillingSpend {
  currency: string;
  available: boolean;
  total: number;
  series: SpendPoint[];
  by_model: SpendByModel[];
  note: string | null;
}

interface Result {
  spend: BillingSpend | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export const useBillingSpend = (days = 30): Result => {
  const [spend, setSpend] = useState<BillingSpend | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<BillingSpend>('/api/billing/spend', { days });
      setSpend({
        currency: data?.currency ?? 'USD',
        available: !!data?.available,
        total: Number(data?.total ?? 0),
        series: Array.isArray(data?.series) ? data.series : [],
        by_model: Array.isArray(data?.by_model) ? data.by_model : [],
        note: data?.note ?? null,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'GET /api/billing/spend failed');
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { reload(); }, [reload]);

  return { spend, loading, error, reload };
};
