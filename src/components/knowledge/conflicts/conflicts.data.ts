import {
  ConflictType,
  Severity as SeverityType,
  ConflictStatus as ConflictStatusType,
  ResolutionMethod,
  CONFLICT_TYPE_LABELS as ENUM_CONFLICT_TYPE_LABELS,
} from '../../../lib/enums';

export type { ConflictType, SeverityType, ConflictStatusType, ResolutionMethod };

// Summary stored per row in the list — conflict_id is kept for reference only, not displayed
export interface ConflictSummary {
  conflict_id: string;
  conflict_type: ConflictType;
  severity: SeverityType;
  detected_at: string;
}

// Pending bucket wraps summaries inside batches
export interface ConflictBatchGroup {
  batch_id: string;
  batch_name: string;
  extracted_date: string;
  number_pending_conflict: number;
  conflicts: ConflictSummary[];
}

// Shape of GET /api/knowledge/conflicts — each bucket is paginated
export interface ConflictsBucket<T> {
  items: T[];
  page: number;
  page_size: number;
  has_next: boolean;
}

export interface ConflictsResponse {
  pending: ConflictsBucket<ConflictBatchGroup> | ConflictBatchGroup[];
  awaiting: ConflictsBucket<ConflictSummary> | ConflictSummary[];
  resolved: ConflictsBucket<ConflictSummary> | ConflictSummary[];
}

// Full record loaded on row click via GET /api/knowledge/conflicts/{conflict_id}
export interface ConflictDetail {
  conflict_id: string;
  conflict_type: ConflictType;
  where_happens: string;
  severity: SeverityType;
  detected_at: string;
  status: ConflictStatusType;
  detailed_explanation: string;
  existing_snapshot: Record<string, unknown>;
  incoming_snapshot: Record<string, unknown>;
  existing_db_content: string | null;
  affected_location: string;
  batch_id: string;
  resolution_instruction?: string;
  selected_resolution_method?: ResolutionMethod;
  resolved_at?: string;
  resolved_by?: string;
}

// One conflict's chosen resolution while assembling a whole-batch submission
export interface BatchConflictChoice {
  method: ResolutionMethod;
  instruction: string;
}

type BatchResolutionMode = 'all_keep_existing' | 'single_keep_incoming' | 'merge' | 'invalid';

// Validate the batch-wide rules (mirrors the backend): keep_existing needs all;
// keep_incoming needs exactly one with the rest keep_existing; merge needs no keep_incoming.
export function classifyBatchResolution(methods: ResolutionMethod[]): BatchResolutionMode {
  if (methods.length === 0) return 'invalid';
  const distinct = new Set(methods);
  if (distinct.size === 1 && distinct.has('keep_existing')) return 'all_keep_existing';
  const keepIncomingCount = methods.filter(method => method === 'keep_incoming').length;
  const onlyExistingOrIncoming = [...distinct].every(method => method === 'keep_incoming' || method === 'keep_existing');
  if (keepIncomingCount === 1 && onlyExistingOrIncoming) return 'single_keep_incoming';
  if (distinct.has('merge') && !distinct.has('keep_incoming')) return 'merge';
  return 'invalid';
}

export const CONFLICT_TYPE_LABELS: Record<ConflictType, { title: string; desc: string }> = {
  table_schema: {
    title: ENUM_CONFLICT_TYPE_LABELS.table_schema,
    desc: 'Same table name but extracted schema is different than stored metadata schema.',
  },
  content_contradiction: {
    title: ENUM_CONFLICT_TYPE_LABELS.content_contradiction,
    desc: 'Content that cannot be true at the same time (strictly requesting to keep one only).',
  },
  content_conflict: {
    title: ENUM_CONFLICT_TYPE_LABELS.content_conflict,
    desc: 'Content that can be true at the same time (allow to be merged).',
  },
  content_duplicate: {
    title: 'Content Duplication',
    desc: 'Content that has same meaning (strictly merge or keep one only for quality of similarity search).',
  },
  content_update: {
    title: ENUM_CONFLICT_TYPE_LABELS.content_update,
    desc: 'Existing data but out of date content (ask to keep one or no action taken).',
  },
};
